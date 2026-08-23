import * as Crypto from 'expo-crypto';

// The Phase 2 contract is explicit: the client still SHA-256s the password and sends the hex
// digest as `passwordHash`; the server stores argon2(passwordHash). So the RN client must
// produce the SAME lowercase-hex SHA-256 of the UTF-8 password bytes that the old
// crypto.subtle.digest path did. expo-crypto uses crypto.subtle on web and native SHA-256
// otherwise, both over UTF-8, both lowercase hex — matching the server's stored legacy hash.
export async function sha256Hex(password: string): Promise<string> {
  return Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, password, {
    encoding: Crypto.CryptoEncoding.HEX,
  });
}
