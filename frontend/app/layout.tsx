import "./globals.css";

export const metadata = {
  title: "Simulink LLM Wiki",
  description: "本地优先、来源可追溯的 Simulink 知识库",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
