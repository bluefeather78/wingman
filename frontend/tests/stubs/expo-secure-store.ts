// SecureStore is the NATIVE half of tokenStore; the stubbed Platform above reports 'web', so
// nothing here is reached. Present so the import resolves.
export async function getItemAsync(_key: string): Promise<string | null> {
  throw new Error('SecureStore reached under Platform.OS === "web"');
}
export async function setItemAsync(_key: string, _value: string): Promise<void> {
  throw new Error('SecureStore reached under Platform.OS === "web"');
}
export async function deleteItemAsync(_key: string): Promise<void> {
  throw new Error('SecureStore reached under Platform.OS === "web"');
}
