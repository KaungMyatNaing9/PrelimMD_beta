import "./styles.css";
export default function Layout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body><main>{children}</main></body></html>; }
