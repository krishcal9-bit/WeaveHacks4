import type { Metadata } from "next";
import Script from "next/script";
import { Fraunces, IBM_Plex_Mono, Outfit } from "next/font/google";
import { CopilotKit } from "@copilotkit/react-core";
import { AppShell } from "@/components/app-shell";
import { ThemeProvider } from "@/components/theme-provider";
import { THEME_STORAGE_KEY } from "@/lib/theme";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Atlas — Autonomous Finance Operations",
  description:
    "An AI finance department that analyzes, debates, and decides on the decisions that move your runway.",
};

const themeInitScript = `
(function () {
  try {
    var stored = localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
    var dark =
      stored === "dark" ||
      (stored !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${outfit.variable} ${fraunces.variable} ${plexMono.variable} h-full`}
    >
      <body className="h-full antialiased" suppressHydrationWarning>
        <Script id="atlas-theme-init" strategy="beforeInteractive">
          {themeInitScript}
        </Script>
        <ThemeProvider>
          {/* agent name must match /api/copilotkit and the LangGraphAGUIAgent name */}
          <CopilotKit
            runtimeUrl="/api/copilotkit"
            agent="finance_department"
            showDevConsole={false}
            enableInspector={false}
          >
            <AppShell>{children}</AppShell>
          </CopilotKit>
        </ThemeProvider>
      </body>
    </html>
  );
}
