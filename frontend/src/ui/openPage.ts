import { Linking, Platform } from 'react-native';
import { backendUrl } from '@/api/httpClient';

// Open a static backend page (terms / privacy / about / pricing / how-we-use-ai).
//
// On WEB, navigate the SAME tab. Linking.openURL maps to window.open(url, '_blank'), which
// mobile browsers popup-block — so a tap on one of these links did nothing. window.location
// is a plain in-page navigation the browser never blocks. On native there is no tab to reuse,
// so hand off to the system browser as before.
export function openBackendPage(path: string): void {
  const url = backendUrl(path);
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    window.location.assign(url);
  } else {
    void Linking.openURL(url);
  }
}
