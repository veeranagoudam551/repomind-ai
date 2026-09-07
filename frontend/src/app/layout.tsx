import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";
import { QueryProvider } from "@/lib/query-provider";
import { getSessionToken } from "@/lib/session";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "RepoMind AI",
  description:
    "AI-powered codebase intelligence — explore, understand, debug, and analyze a GitHub repository grounded in its real source code.",
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const token = await getSessionToken();

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <QueryProvider>
          <SiteHeader isAuthenticated={Boolean(token)} />
          <div className="flex flex-1 flex-col">{children}</div>
          <SiteFooter />
        </QueryProvider>
      </body>
    </html>
  );
}
