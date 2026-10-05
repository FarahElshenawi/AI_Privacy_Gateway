import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Fraunces } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  style: ["normal", "italic"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Doppel — Your data stays on your device",
  description:
    "The privacy layer for the AI tools your teams already use. Sensitive information is masked before it ever leaves the browser.",
  keywords: [
    "Doppel",
    "AI Privacy",
    "Data Masking",
    "Enterprise AI Governance",
    "Data Loss Prevention",
  ],
  authors: [{ name: "Doppel" }],
  openGraph: {
    title: "Doppel — Your data stays on your device",
    description:
      "The privacy layer for the AI tools your teams already use. Sensitive information is masked before it ever leaves the browser.",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "Doppel",
    description:
      "The privacy layer for the AI tools your teams already use.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning className="dark">
      <body
        className={`${inter.variable} ${jetbrainsMono.variable} ${fraunces.variable} antialiased`}
      >
        {children}
        <Toaster />
      </body>
    </html>
  );
}
