"use client";

import { useState, useRef, useEffect } from "react";
import ChatBubble from "@/components/ChatBubble";
import ChatInput from "@/components/ChatInput";
import { sendChat, type ChatResponse } from "@/lib/api";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: ChatResponse["citations"];
  confidence?: number;
  debug?: ChatResponse["debug"];
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDebug, setShowDebug] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  const handleSend = async (text: string) => {
    setError(null);
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);

    try {
      const resp = await sendChat({ query: text });
      const botMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: resp.answer,
        citations: resp.citations,
        confidence: resp.confidence,
        debug: resp.debug,
      };
      setMessages((prev) => [...prev, botMsg]);
    } catch (e: any) {
      setError(e.message || "Something went wrong. Is the backend running?");
    } finally {
      setLoading(false);
    }
  };

  const handleClear = () => {
    setMessages([]);
    setError(null);
  };

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <header className="bg-gt-navy text-white px-4 py-3 flex items-center justify-between shadow-md">
        <div className="flex items-center gap-2">
          <span className="text-2xl">🐝</span>
          <div>
            <h1 className="text-lg font-bold leading-tight">BuzzBot</h1>
            <p className="text-xs text-gt-gold opacity-80">Georgia Tech Assistant</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowDebug(!showDebug)}
            className={`text-xs px-2 py-1 rounded transition ${
              showDebug ? "bg-gt-gold text-gt-navy" : "bg-white/10 hover:bg-white/20"
            }`}
          >
            Debug
          </button>
          <button
            onClick={handleClear}
            className="text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition"
          >
            Clear
          </button>
        </div>
      </header>

      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
        <div className="max-w-3xl mx-auto">
          {messages.length === 0 && !loading && (
            <div className="text-center mt-20">
              <span className="text-6xl">🐝</span>
              <h2 className="text-xl font-semibold text-gt-navy mt-4">
                Welcome to BuzzBot!
              </h2>
              <p className="text-gray-500 mt-2 max-w-md mx-auto">
                I can help you with Georgia Tech registration, course catalog, academic
                calendar, and more. Ask me anything!
              </p>
              <div className="flex flex-wrap justify-center gap-2 mt-6">
                {[
                  "When is the registration deadline?",
                  "Tell me about CS 1332",
                  "What are the degree requirements for CS?",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="text-sm px-3 py-2 rounded-full border border-gt-gold text-gt-navy
                               hover:bg-gt-gold/10 transition"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <ChatBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              citations={msg.citations}
              confidence={msg.confidence}
              debug={msg.debug}
              showDebug={showDebug}
            />
          ))}

          {loading && (
            <div className="flex justify-start mb-4">
              <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm">
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <span className="animate-pulse">🐝</span>
                  BuzzBot is thinking…
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border-t border-red-200 px-4 py-2 text-center">
          <p className="text-sm text-red-600">{error}</p>
          <button
            onClick={() => setError(null)}
            className="text-xs text-red-400 hover:text-red-600 mt-0.5"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Input */}
      <ChatInput onSend={handleSend} disabled={loading} />
    </div>
  );
}
