"use client";

import { useState } from "react";
import type { Citation } from "@/lib/api";

interface Props {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  confidence?: number;
  debug?: { intent: string | null; live_fetch_used: boolean; retrieval_top_k: number };
  showDebug?: boolean;
}

export default function ChatBubble({
  role,
  content,
  citations,
  confidence,
  debug,
  showDebug,
}: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const isUser = role === "user";

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      <div
        className={`max-w-[85%] md:max-w-[70%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-gt-navy text-white rounded-br-sm"
            : "bg-white border border-gray-200 shadow-sm rounded-bl-sm"
        }`}
      >
        {/* Avatar */}
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold opacity-70">
            {isUser ? "You" : "🐝 BuzzBot"}
          </span>
          {confidence !== undefined && !isUser && (
            <span
              className={`text-xs px-1.5 py-0.5 rounded-full ${
                confidence >= 0.7
                  ? "bg-green-100 text-green-700"
                  : confidence >= 0.4
                  ? "bg-yellow-100 text-yellow-700"
                  : "bg-red-100 text-red-700"
              }`}
            >
              {Math.round(confidence * 100)}%
            </span>
          )}
        </div>

        {/* Content */}
        <div className="text-sm leading-relaxed whitespace-pre-wrap">{content}</div>

        {/* Actions for assistant messages */}
        {!isUser && (
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            <button
              onClick={handleCopy}
              className="text-xs text-gray-400 hover:text-gray-600 transition"
            >
              {copied ? "Copied!" : "Copy"}
            </button>

            {citations && citations.length > 0 && (
              <button
                onClick={() => setSourcesOpen(!sourcesOpen)}
                className="text-xs text-gt-blue hover:underline transition"
              >
                {sourcesOpen ? "Hide sources" : `Sources (${citations.length})`}
              </button>
            )}
          </div>
        )}

        {/* Sources accordion */}
        {sourcesOpen && citations && citations.length > 0 && (
          <div className="mt-2 border-t border-gray-100 pt-2 space-y-2">
            {citations.map((cit, i) => (
              <div key={i} className="text-xs bg-gray-50 rounded-lg p-2">
                <div className="flex items-center gap-1">
                  <span className="font-medium text-gt-navy">
                    {cit.title || "Source"}
                  </span>
                  {cit.fetched_at && (
                    <span className="text-gray-400">
                      · {new Date(cit.fetched_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
                <p className="text-gray-500 mt-0.5 italic">"{cit.quote}"</p>
                {cit.url && !cit.url.startsWith("user-provided") && (
                  <a
                    href={cit.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-gt-blue hover:underline mt-0.5 inline-block"
                  >
                    {cit.url.length > 60 ? cit.url.slice(0, 60) + "…" : cit.url}
                  </a>
                )}
                {cit.url?.startsWith("user-provided") && (
                  <span className="text-orange-500 mt-0.5 inline-block">
                    User-provided content (unofficial)
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Debug info */}
        {showDebug && debug && !isUser && (
          <div className="mt-2 border-t border-gray-100 pt-1 text-xs text-gray-400">
            Intent: {debug.intent} | Live fetch: {debug.live_fetch_used ? "Yes" : "No"} |
            Chunks: {debug.retrieval_top_k}
          </div>
        )}
      </div>
    </div>
  );
}
