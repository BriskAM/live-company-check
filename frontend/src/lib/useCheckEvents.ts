"use client";

import { useEffect, useReducer, useState } from "react";
import { API_URL } from "./api";
import { CheckEvent, CheckView, initialView, reduce } from "./checkState";

type Action = { type: "event"; event: CheckEvent } | { type: "reset" };

function reducer(view: CheckView, action: Action): CheckView {
  return action.type === "reset" ? initialView : reduce(view, action.event);
}

/**
 * Subscribe to a check's event stream. The browser's EventSource reconnects on its own and
 * sends Last-Event-ID, so the server resumes exactly after the last event we received.
 */
export function useCheckEvents(checkId: string) {
  const [view, dispatch] = useReducer(reducer, initialView);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    dispatch({ type: "reset" });
    const source = new EventSource(`${API_URL}/checks/${checkId}/events`);
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (msg) => dispatch({ type: "event", event: JSON.parse(msg.data) });
    return () => source.close();
  }, [checkId]);

  return { view, connected };
}
