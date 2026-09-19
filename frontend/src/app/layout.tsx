import "./styles.css";
import type {Metadata} from "next";
// The tab has a name and a mark: a demo spends its whole life in a browser tab, and an
// untitled one with a missing-icon 404 beside it is the first thing an audience sees.
export const metadata: Metadata = {
  title: "PrelimMD — intake forms, read and refilled",
  description:
    "Reads a filled intake form, flags what it is unsure of for a human, and refills a blank "
    + "copy of the same form. Research prototype; synthetic data only.",
};
export default function Layout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body><main>{children}</main></body></html>; }
