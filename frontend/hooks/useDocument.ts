"use client";

// Typed single-document subscription (specs/05 §5.6). Detail screens subscribe to
// a specific document (e.g. `loanWorkbenches/{loanId}`) — never a global collection.
// Exposes loading / error / data so every screen can render the required
// loading / empty / error states (specs/15 §15.2).

import { doc, onSnapshot } from "firebase/firestore";
import { useEffect, useState } from "react";
import { getFirebaseDb } from "@/lib/firebase";

export interface DocumentState<T> {
  data: T | null;
  loading: boolean;
  error: Error | null;
  /** true once the subscription has resolved and the document does not exist. */
  empty: boolean;
}

/**
 * Subscribe to a single Firestore document by collection + id.
 * Pass a null/empty id to stay idle (e.g. before a route param is known).
 */
export function useDocument<T>(
  collectionPath: string,
  docId: string | null | undefined,
): DocumentState<T> {
  const [state, setState] = useState<DocumentState<T>>({
    data: null,
    loading: Boolean(docId),
    error: null,
    empty: false,
  });

  useEffect(() => {
    if (!docId) {
      setState({ data: null, loading: false, error: null, empty: true });
      return;
    }
    setState({ data: null, loading: true, error: null, empty: false });

    const ref = doc(getFirebaseDb(), collectionPath, docId);
    const unsubscribe = onSnapshot(
      ref,
      (snap) => {
        setState({
          data: snap.exists() ? ({ id: snap.id, ...snap.data() } as T) : null,
          loading: false,
          error: null,
          empty: !snap.exists(),
        });
      },
      (err) => setState({ data: null, loading: false, error: err, empty: false }),
    );

    return unsubscribe;
  }, [collectionPath, docId]);

  return state;
}
