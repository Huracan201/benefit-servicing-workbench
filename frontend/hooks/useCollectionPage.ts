"use client";

// Paginated, index-backed collection subscription (specs/05 §5.6, specs/13).
//
// This hook is the ENFORCEMENT POINT for the normative subscription rules:
//   - every list subscription MUST carry a `limit` (bounded, never a whole collection);
//   - it MUST be scoped by an indexed predicate (where/orderBy) rather than
//     client-side filtering a broad query;
//   - it MUST paginate with a cursor (startAfter the last visible doc).
//
// Callers describe the query declaratively (constraints + limit + optional cursor);
// the hook builds the query and manages the live subscription for the current page.

import {
  type DocumentData,
  type QueryConstraint,
  type QueryDocumentSnapshot,
  collection,
  limit as limitFn,
  onSnapshot,
  query,
  startAfter,
} from "firebase/firestore";
import { useEffect, useRef, useState } from "react";
import { getFirebaseDb } from "@/lib/firebase";

/** Firestore's UI table page size (specs/21 §21.1: UI tables 25). */
export const DEFAULT_PAGE_SIZE = 25;
/** Hard ceiling so a caller can never request an unbounded page (specs/05 §5.6). */
export const MAX_PAGE_SIZE = 200;

export interface CollectionPageOptions {
  /** where(...) / orderBy(...) constraints — must be index-backed (specs/13). */
  constraints: QueryConstraint[];
  /** Page size; clamped to [1, MAX_PAGE_SIZE]. Defaults to 25. */
  pageSize?: number;
  /** Cursor: the last document of the previous page (from `lastVisible`). */
  cursor?: QueryDocumentSnapshot<DocumentData> | null;
  /** Set false to pause the subscription. */
  enabled?: boolean;
  /**
   * Optional STABLE identity for the logical query (e.g. a primitive filter key).
   * When provided it — not the `constraints` array's object identity — decides when
   * the live listener re-subscribes, so an accidentally unmemoized `constraints`
   * array can never force a re-subscribe (a full billed re-read). Omit to keep the
   * legacy behavior of keying on the `constraints` array identity.
   */
  queryKey?: string;
}

export interface CollectionPageState<T> {
  items: T[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
  /** Cursor for the NEXT page — pass back in as `cursor`. Null when no page loaded. */
  lastVisible: QueryDocumentSnapshot<DocumentData> | null;
  /** True iff a further page actually exists (exact — we look one row ahead). */
  hasMore: boolean;
}

function clampPageSize(pageSize?: number): number {
  const n = pageSize ?? DEFAULT_PAGE_SIZE;
  if (!Number.isFinite(n) || n < 1) return DEFAULT_PAGE_SIZE;
  return Math.min(Math.floor(n), MAX_PAGE_SIZE);
}

/**
 * Subscribe to one page of a collection. The query is always bounded by `limit`
 * and scoped by the provided (indexed) constraints — an unbounded collection
 * subscription is not expressible through this hook by design.
 */
export function useCollectionPage<T>(
  collectionPath: string,
  options: CollectionPageOptions,
): CollectionPageState<T> {
  const { constraints, pageSize, cursor, enabled = true, queryKey } = options;
  const size = clampPageSize(pageSize);

  // Stable key so we only re-subscribe when the effective query changes. Query
  // constraints don't expose a value we can read, so callers should keep the
  // `constraints` array referentially stable per logical query (e.g. via useMemo).
  const constraintsRef = useRef(constraints);
  constraintsRef.current = constraints;

  // Key the listener on the cursor's STABLE doc path, not the snapshot object.
  // Every snapshot delivery re-mints `lastVisible` as a fresh QueryDocumentSnapshot
  // (below), so depending on the cursor OBJECT would tear down and re-subscribe the
  // listener on every delivery — re-billing the whole limit(size+1) page ~20×/sec
  // for an otherwise-idle subscription. The path is identical for a re-delivered
  // snapshot of the same doc, so keying on it makes an idle subscription cost ~0.
  const cursorPath = cursor ? cursor.ref.path : null;
  // Hold the live snapshot in a ref so the effect can still call startAfter with the
  // REAL QueryDocumentSnapshot (mirroring constraintsRef) while depending only on the
  // path string.
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;

  const [state, setState] = useState<CollectionPageState<T>>({
    items: [],
    loading: enabled,
    error: null,
    empty: false,
    lastVisible: null,
    hasMore: false,
  });

  useEffect(() => {
    if (!enabled) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    setState((s) => ({ ...s, loading: true, error: null }));

    const parts: QueryConstraint[] = [...constraintsRef.current];
    if (cursorRef.current) parts.push(startAfter(cursorRef.current));
    // Look one row past the page so `hasMore` is EXACT (never enable Next into an
    // empty page when the total is a multiple of the page size). The lookahead row
    // is trimmed below, so the cursor stays the last SHOWN doc (semantics preserved).
    parts.push(limitFn(size + 1));

    const q = query(collection(getFirebaseDb(), collectionPath), ...parts);
    const unsubscribe = onSnapshot(
      q,
      (snap) => {
        const hasMore = snap.docs.length > size;
        const pageDocs = hasMore ? snap.docs.slice(0, size) : snap.docs;
        const items = pageDocs.map((d) => ({ id: d.id, ...d.data() }) as T);
        setState({
          items,
          loading: false,
          error: null,
          empty: pageDocs.length === 0,
          lastVisible: pageDocs.length ? pageDocs[pageDocs.length - 1] : null,
          hasMore,
        });
      },
      (err) =>
        setState({
          items: [],
          loading: false,
          error: err,
          empty: false,
          lastVisible: null,
          hasMore: false,
        }),
    );

    return unsubscribe;
    // NOTE: each page keeps its own live onSnapshot listener, so a document that
    // changes sort position can shift between adjacent pages (transient dup/gap).
    // This real-time + cursor design is what specs/05 §5.6 mandates; a screen that
    // needs strictly stable pagination should swap this for a one-shot getDocs.
    //
    // Re-subscribe only when the LOGICAL query changes: the path, the effective query
    // (`queryKey` when supplied, else the constraints array identity), the page size,
    // the cursor DOC PATH (not the re-minted snapshot object), or enabled. The live
    // constraints/cursor snapshots are read from their refs inside the effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collectionPath, queryKey ?? constraints, size, cursorPath, enabled]);

  return state;
}
