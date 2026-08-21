"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ChatMessage,
  makeMessage,
  readStoredMessages,
  SCIENTIFIC_CHAT_STORAGE_KEY,
  SCIENTIFIC_CHAT_SYNC_EVENT,
  ScientificChatResponse,
  storeMessages,
} from "@/components/chat/chat-model";

async function parseError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return typeof payload?.detail === "string" ? payload.detail : JSON.stringify(payload);
  } catch {
    return `${response.status} ${response.statusText}`.trim();
  }
}

export function useScientificChat(sessionId: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setMessages(readStoredMessages());
    const sync = (event: Event) => {
      const detail = (event as CustomEvent<ChatMessage[]>).detail;
      setMessages(Array.isArray(detail) ? detail : readStoredMessages());
    };
    const storage = (event: StorageEvent) => {
      if (event.key === SCIENTIFIC_CHAT_STORAGE_KEY) setMessages(readStoredMessages());
    };
    window.addEventListener(SCIENTIFIC_CHAT_SYNC_EVENT, sync);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener(SCIENTIFIC_CHAT_SYNC_EVENT, sync);
      window.removeEventListener("storage", storage);
    };
  }, []);

  const send = useCallback(async (rawMessage: string) => {
    const content = rawMessage.trim();
    if (!content || isLoading) return;
    const userMessage = makeMessage("user", content);
    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    storeMessages(nextMessages);
    setError(null);
    setIsLoading(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const history = messages.slice(-12).map((message) => ({ role: message.role, content: message.content.slice(0, 4_000) }));
      let body: BodyInit;
      let headers: HeadersInit | undefined;
      if (attachments.length) {
        const form = new FormData();
        form.append("message", content);
        form.append("history", JSON.stringify(history));
        if (sessionId) form.append("current_session_id", sessionId);
        attachments.forEach((file) => form.append("files", file, file.name));
        body = form;
      } else {
        headers = { "Content-Type": "application/json" };
        body = JSON.stringify({ message: content, history, current_session_id: sessionId });
      }
      const response = await fetch("/api/chat", {
        method: "POST",
        headers,
        body,
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await parseError(response));
      const payload = await response.json() as ScientificChatResponse;
      const completed = [...nextMessages, makeMessage("assistant", payload.message, payload)];
      setMessages(completed);
      storeMessages(completed);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") {
        setError("Request cancelled. Your message remains in the conversation.");
      } else {
        setError(cause instanceof Error ? cause.message : "The assistant request failed.");
      }
    } finally {
      abortRef.current = null;
      setIsLoading(false);
    }
  }, [attachments, isLoading, messages, sessionId]);

  const addAttachments = useCallback((selected: File[]) => {
    const excelFiles = selected.filter((file) => /\.(xls|xlsx)$/i.test(file.name));
    if (excelFiles.length !== selected.length) {
      setError("Only .xls and .xlsx workbooks can be attached.");
      return;
    }
    const next = [...attachments];
    for (const file of excelFiles) {
      if (next.length >= 5) {
        setError("You can attach at most 5 workbooks.");
        return;
      }
      const duplicate = next.some((item) =>
        item.name === file.name && item.size === file.size && item.lastModified === file.lastModified
      );
      if (!duplicate) next.push(file);
    }
    const totalBytes = next.reduce((sum, file) => sum + file.size, 0);
    if (totalBytes > 25 * 1024 * 1024) {
      setError("Excel attachments must be 25 MB or less in total.");
      return;
    }
    setAttachments(next);
    setError(null);
  }, [attachments]);

  const removeAttachment = useCallback((index: number) => {
    setAttachments((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setAttachments([]);
    setError(null);
    storeMessages([]);
  }, []);

  const cancel = useCallback(() => abortRef.current?.abort(), []);

  return {
    messages,
    attachments,
    isLoading,
    error,
    send,
    clear,
    cancel,
    addAttachments,
    removeAttachment,
  };
}
