import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { CopilotKit } from "@copilotkit/react-core";
import { AppShell } from "@/components/app-shell";
import "@copilotkit/react-ui/styles.css";
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
  title: "Atlas — Autonomous Finance Operations",
  description:
    "An AI finance department that analyzes, debates, and decides on the decisions that move your runway.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full`}
    >
      <body className="h-full antialiased">
        {/* agent name must match /api/copilotkit and the LangGraphAGUIAgent name */}
        <CopilotKit runtimeUrl="/api/copilotkit" agent="finance_department">
          <AppShell>{children}</AppShell>
        </CopilotKit>
      </body>
    </html>
  );
}
