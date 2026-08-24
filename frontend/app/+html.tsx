import { ScrollViewStyleReset } from 'expo-router/html';
import { type PropsWithChildren } from 'react';

// Root HTML document for web. Without this file, expo-router falls back to a bare template
// that carries no <link rel="icon"> at all — the favicon.ico that app.json's web.favicon
// generates is served correctly (confirmed: GET /favicon.ico 200s in both `expo start --web`
// and the static export), nothing in the page ever references it. `expo export -p web`
// happens to inject the tag itself as a build step, which is why the bug only showed up in
// dev — this file makes the tag explicit so both paths agree.
export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
        <link rel="icon" href="/favicon.ico" />
        <ScrollViewStyleReset />
      </head>
      <body>{children}</body>
    </html>
  );
}
