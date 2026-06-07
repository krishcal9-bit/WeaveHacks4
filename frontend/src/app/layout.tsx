import type { Metadata } from "next";
import Script from "next/script";
import { Archivo, Fraunces, IBM_Plex_Mono, Newsreader } from "next/font/google";
import { CopilotKit } from "@copilotkit/react-core";
import { AppShell } from "@/components/app-shell";
import { ThemeProvider } from "@/components/theme-provider";
import { APP_DESCRIPTION, APP_DOCUMENT_TITLE } from "@/lib/branding";
import { THEME_STORAGE_KEY } from "@/lib/theme";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";

// Grotesque for dense UI / data — sharper, more editorial than a neutral sans.
const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
});

// High-contrast display serif for mastheads and headlines (kept, leaned into).
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  style: ["normal", "italic"],
});

// Editorial reading serif for ledes, memo prose, and transcript bodies.
const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  style: ["normal", "italic"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: APP_DOCUMENT_TITLE,
  description: APP_DESCRIPTION,
  applicationName: APP_DOCUMENT_TITLE,
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
      className={`${archivo.variable} ${fraunces.variable} ${newsreader.variable} ${plexMono.variable} h-full`}
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
