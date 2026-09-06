// The 50 US states (plus DC) used to power the type-ahead helper on the "where are you
// based?" question and the optional Home State search field. Kept as one flat list so the
// finder screen and anything else that asks for a state suggest exactly the same names.
export const US_STATES = [
  'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut',
  'Delaware', 'District of Columbia', 'Florida', 'Georgia', 'Hawaii', 'Idaho', 'Illinois',
  'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine', 'Maryland', 'Massachusetts',
  'Michigan', 'Minnesota', 'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
  'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina', 'North Dakota',
  'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania', 'Rhode Island', 'South Carolina',
  'South Dakota', 'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
  'West Virginia', 'Wisconsin', 'Wyoming',
] as const;

// USPS two-letter codes, indexed by state name, so a student who types "wa" or "NY" gets the
// obvious match even though the suggestion list shows full names.
export const US_STATE_ABBR: Record<string, string> = {
  Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA',
  Colorado: 'CO', Connecticut: 'CT', Delaware: 'DE', 'District of Columbia': 'DC',
  Florida: 'FL', Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL', Indiana: 'IN',
  Iowa: 'IA', Kansas: 'KS', Kentucky: 'KY', Louisiana: 'LA', Maine: 'ME', Maryland: 'MD',
  Massachusetts: 'MA', Michigan: 'MI', Minnesota: 'MN', Mississippi: 'MS', Missouri: 'MO',
  Montana: 'MT', Nebraska: 'NE', Nevada: 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
  'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND',
  Ohio: 'OH', Oklahoma: 'OK', Oregon: 'OR', Pennsylvania: 'PA', 'Rhode Island': 'RI',
  'South Carolina': 'SC', 'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT',
  Vermont: 'VT', Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI',
  Wyoming: 'WY',
};

// Suggest states for what the student has typed so far. The query is matched against the last
// comma-separated chunk (so "Seattle, wa" still suggests Washington), against both the full
// name and the two-letter code, and prefers prefix matches before mid-word ones. An empty or
// already-exact query yields nothing so the dropdown stays hidden.
export function suggestStates(input: string, limit = 6): string[] {
  const tail = input.split(',').pop() ?? input;
  const q = tail.trim().toLowerCase();
  if (!q) return [];
  // Don't keep suggesting once the chunk already is a state name.
  if (US_STATES.some((s) => s.toLowerCase() === q)) return [];
  const starts: string[] = [];
  const contains: string[] = [];
  for (const state of US_STATES) {
    const name = state.toLowerCase();
    const abbr = (US_STATE_ABBR[state] ?? '').toLowerCase();
    if (name.startsWith(q) || abbr === q) starts.push(state);
    else if (name.includes(q)) contains.push(state);
  }
  return [...starts, ...contains].slice(0, limit);
}
