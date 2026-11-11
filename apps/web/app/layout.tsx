import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Documentation platform",
  description: "Analyze a site or codebase and generate documentation from observed results.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
