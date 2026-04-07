import type { Metadata } from "next";
import "./globals.css";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/layout/app-shell";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: "ClipForge — AI Video Clipping Studio",
  description:
    "Local-first AI tool that extracts viral moments from long-form video and exports social-ready vertical clips.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">
        <Providers>
          <TooltipProvider>
            <AppShell>{children}</AppShell>
            <Toaster
              position="bottom-right"
              toastOptions={{
                style: {
                  background: "oklch(0.17 0.012 260)",
                  border: "1px solid oklch(0.26 0.012 260 / 0.5)",
                  color: "oklch(0.95 0.01 260)",
                },
              }}
            />
          </TooltipProvider>
        </Providers>
      </body>
    </html>
  );
}
