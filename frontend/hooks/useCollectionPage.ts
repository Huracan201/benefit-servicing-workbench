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
import { useEffect, useMemo, useRef, useState } from "react";
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
}

export interface CollectionPageState<T> {
  items: T[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
  /** Cursor for the NEXT page — pass back in as `cursor`. Null when no page loaded. */
  lastVisible: QueryDocumentSnapshot<DocumentData> | null;
  /** Heuristic: a full page was returned, so another page may exist. */
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
  const { constraints, pageSize, cursor, enabled = true } = options;
  const size = clampPageSize(pageSize);

  // Stable key so we only re-subscribe when the effective query changes. Query
  // constraints don't expose a value we can read, so callers should keep the
  // `constraints` array referentially stable per logical query (e.g. via useMemo).
  const constraintsRef = useRef(constraints);
  constraintsRef.current = constraints;

  const [state, setState] = useState<CollectionPageState<T>>({
    items: [],
    loading: enabled,
    error: null,
    empty: false,
    lastVisible: null,
    hasMore: false,
  });

  // Depend on identity of the constraints array + primitive knobs.
  const deps = useMemo(
    () => [collectionPath, constraints, size, cursor, enabled] as const,
    [collectionPath, constraints, size, cursor, enabled],
  );

  useEffect(() => {
    if (!enabled) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    setState((s) => ({ ...s, loading: true, error: null }));

    const parts: QueryConstraint[] = [...constraintsRef.current];
    if (cursor) parts.push(startAfter(cursor));
    parts.push(limitFn(size));

    const q = query(collection(getFirebaseDb(), collectionPath), ...parts);
    const unsubscribe = onSnapshot(
      q,
      (snap) => {
        const items = snap.docs.map((d) => ({ id: d.id, ...d.data() }) as T);
        setState({
          items,
          loading: false,
          error: null,
          empty: snap.empty,
          lastVisible: snap.docs.length ? snap.docs[snap.docs.length - 1] : null,
          hasMore: snap.docs.length === size,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
