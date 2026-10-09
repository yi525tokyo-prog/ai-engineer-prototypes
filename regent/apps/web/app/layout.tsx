import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Regent Cockpit",
  description: "Operational cockpit for Regent: world state, competing routes, execution and bounded human actions.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
