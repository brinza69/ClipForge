"use client";

import { useState, useCallback } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  Link2, ArrowRight, Loader2, Play, Tv2, Video,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { motion, AnimatePresence } from "framer-motion";

interface UrlInputProps {
  onProjectCreated?: (projectId: string) => void;
}

export function UrlInput({ onProjectCreated }: UrlInputProps) {
  const [url, setUrl] = useState("");
  const [isFocused, setIsFocused] = useState(false);
  const [loadingText, setLoadingText] = useState("Analyzing...");

  const mutation = useMutation({
    mutationFn: (sourceUrl: string) =>
      api.projects.create({ source_url: sourceUrl }),
    onSuccess: (data) => {
      toast.success("Project created", {
        description: "Fetching video metadata...",
      });
      setUrl("");
      onProjectCreated?.(data.id);
    },
    onError: (error: Error) => {
      toast.error("Failed to create project", {
        description: error.message,
      });
    },
  });

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const trimmed = url.trim();
      if (!trimmed) return;
      
      // Start rotating text
      const messages = ["Analyzing...", "Fetching Metadata...", "Extracting info..."];
      let i = 1;
      setLoadingText(messages[0]);
      
      const interval = setInterval(() => {
        setLoadingText(messages[i]);
        i = (i + 1) % messages.length;
      }, 1500);

      const restore = () => clearInterval(interval);

      mutation.mutate(trimmed, {
        onSettled: restore,
      });
    },
    [url, mutation],
  );

  const detectPlatform = (input: string) => {
    if (input.includes("youtube.com") || input.includes("youtu.be")) return "youtube";
    if (input.includes("twitch.tv")) return "twitch";
    if (input.includes("vimeo.com")) return "vimeo";
    return null;
  };

  const platform = detectPlatform(url);

  return (
    <form onSubmit={handleSubmit}>
      <div
        className={cn(
          "glass relative overflow-hidden rounded-2xl transition-all duration-300",
          isFocused && "glow-primary border-primary/40",
        )}
      >
        <div className="flex items-center gap-3 px-5 py-4">
          {/* Platform icon */}
          <AnimatePresence mode="wait">
            <motion.div
              key={platform || "default"}
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={{ duration: 0.15 }}
              className="flex-shrink-0"
            >
              {platform === "youtube" ? (
                <Play className="h-5 w-5 text-red-400" />
              ) : platform === "twitch" ? (
                <Tv2 className="h-5 w-5 text-purple-400" />
              ) : platform === "vimeo" ? (
                <Video className="h-5 w-5 text-cyan-400" />
              ) : (
                <Link2 className="h-5 w-5 text-muted-foreground" />
              )}
            </motion.div>
          </AnimatePresence>

          {/* Input */}
          <input
            type="url"
            placeholder="Paste a video link — YouTube, Twitch, Vimeo, or direct URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            className="flex-1 bg-transparent text-sm text-foreground placeholder-muted-foreground/60 outline-none"
            autoComplete="off"
          />

          {/* Submit */}
          <Button
            type="submit"
            disabled={!url.trim() || mutation.isPending}
            size="sm"
            className="gap-2 rounded-xl bg-primary px-4 font-semibold shadow-lg shadow-primary/20 transition-all hover:shadow-xl hover:shadow-primary/30"
          >
            {mutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>{loadingText}</span>
              </>
            ) : (
              <>
                Analyze
                <ArrowRight className="h-3.5 w-3.5" />
              </>
            )}
          </Button>
        </div>

        {/* Supported platforms hint */}
        {isFocused && !url && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="border-t border-border/30 px-5 py-3"
          >
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="font-medium">Supported:</span>
              <span className="flex items-center gap-1">
                <Play className="h-3 w-3 text-red-400" /> YouTube
              </span>
              <span className="flex items-center gap-1">
                <Tv2 className="h-3 w-3 text-purple-400" /> Twitch
              </span>
              <span className="flex items-center gap-1">
                <Video className="h-3 w-3 text-cyan-400" /> Vimeo
              </span>
              <span>• Direct MP4 links</span>
            </div>
          </motion.div>
        )}
      </div>
    </form>
  );
}
