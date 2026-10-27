export {};

declare global {
  var __heyyerpTestMailSink: ((mail: { to: string; subject: string; text: string; html?: string }) => void) | undefined;
}
