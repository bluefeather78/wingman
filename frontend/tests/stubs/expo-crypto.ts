// hash.ts uses expo-crypto for the login password digest. Node has the same primitive, so the
// stub is a real implementation rather than a throw — a test that hashes gets the right answer.
import { createHash } from 'node:crypto';

export const CryptoDigestAlgorithm = { SHA256: 'SHA-256' } as const;
export const CryptoEncoding = { HEX: 'hex' } as const;

export async function digestStringAsync(
  _algorithm: string,
  data: string,
  _options?: unknown,
): Promise<string> {
  return createHash('sha256').update(data, 'utf8').digest('hex');
}
