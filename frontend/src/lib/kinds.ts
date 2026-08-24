// Kind configuration ported from script.js. The Finder's Stage-0 choice drives Stage-1's
// form. This is the data half of the old KIND_CONFIG; the DOM renderers (renderKindGrid,
// selectKind) are rebuilt as components. `source: 'web'` / `venueKind` are retained for the
// currently-unused web-search venue path.

export interface KindConfig {
  name: string;
  desc: string;
  source: 'local' | 'web';
  dbTypes?: string[];
  strictType?: boolean;
  comingSoon?: boolean;
  heading: string;
  sub: string;
  label: string;
  placeholder: string;
  venueKind?: string;
}

export const KIND_CONFIG: Record<string, KindConfig> = {
  summer: {
    name: 'Summer Program',
    desc: 'Camps, pre-college programs, and summer academies',
    source: 'local',
    dbTypes: ['Program'],
    heading: 'What are your interests?',
    sub: "Tell us what excites you — subjects, hobbies, activities, or things you'd love to explore. The more specific, the better the matches.",
    label: 'What are you interested in?',
    placeholder: "e.g. I love robotics and want to get hands-on with building and programming robots. I'm also curious about biology, especially genetics, and I enjoy creative writing on the side...",
  },
  internship: {
    name: 'Internship',
    desc: 'Hands-on positions with mentors, labs, or organizations',
    source: 'local',
    dbTypes: ['Internship'],
    heading: 'What are your interests and what kind of experience are you looking for?',
    sub: "Tell us the field you want to work in, any relevant skills or coursework you already have, and what kind of hands-on experience you're hoping to gain.",
    label: 'Your interests and target experience',
    placeholder: "e.g. I'm interested in biomedical research, especially cancer biology. I've taken AP Biology and Chemistry and done independent reading on immunotherapy. I'm looking for a lab position where I can get real hands-on research experience...",
  },
  conference: {
    name: 'Conference Venue',
    desc: 'Academic workshops and conferences to submit a paper to',
    source: 'local',
    dbTypes: ['Conference'],
    // Only a handful of Conference-typed rows exist — always hard-filter to just those
    // rather than falling back to the full database (see preFilter's strict param).
    strictType: true,
    heading: 'Describe your research',
    sub: "Tell us what your research is about, the methods or approach you used, and what stage it's at (early idea, in progress, or a finished paper ready to submit).",
    label: 'Describe your research',
    placeholder: 'e.g. My research investigates whether large language models encode Hindi grammatical case roles (kāraka) independently of surface case marking. I use linear probing and LEACE causal concept erasure on mBERT, HindBERT, and MuRIL...',
  },
  journal: {
    name: 'Journal Venue',
    desc: 'Academic and student journals to publish a paper in',
    source: 'local',
    dbTypes: ['Journal'],
    strictType: true,
    heading: 'Describe your research',
    sub: "Tell us what your research is about, the methods or approach you used, and what stage it's at (early idea, in progress, or a finished paper ready to submit).",
    label: 'Describe your research',
    placeholder: 'e.g. My research develops a grapheme-to-phoneme system for three endangered Finnic languages — Karelian, Livonian, and Ingrian — comparing rule-based and neural approaches...',
  },
  'research-competition': {
    name: 'Research or Project Competition',
    desc: 'Science fairs, app challenges, and project-based contests',
    source: 'local',
    dbTypes: ['Research'],
    heading: 'Describe your project',
    sub: "Tell us what you've built or researched, the techniques or skills involved, and what makes it worth entering into a competition.",
    label: 'Describe your project',
    placeholder: 'e.g. I built an AI-powered app that helps autistic children practice reading comprehension, using a speech recognition model fine-tuned on atypical speech and a visual system that shows images and asks kids questions about them out loud...',
  },
  // The catalog's ~25 `Volunteer` rows had no kind covering them, so they were reachable
  // ONLY through profile suggestions and were filed as summer camps once saved. Note the
  // Quest Log has six fixed buckets and none of them is volunteering — findBucketForKind
  // maps this to `internships`, which is what a volunteer placement actually resembles.
  volunteer: {
    name: 'Volunteering',
    desc: 'Service roles and volunteer placements with an organization',
    source: 'local',
    dbTypes: ['Volunteer'],
    heading: 'What kind of volunteering are you looking for?',
    sub: "Tell us the causes you care about, any skills you'd bring, and how much time you can give.",
    label: 'What you care about and want to do',
    placeholder: "e.g. I care a lot about food insecurity in my city and want to help out somewhere hands-on. I'm comfortable with spreadsheets and social media, and I have free weekends during the school year...",
  },
  'pure-competition': {
    name: 'Academic Competition',
    desc: 'Skills or knowledge tests — olympiads, quiz bowls, exams',
    source: 'local',
    dbTypes: ['Competition'],
    heading: 'Describe your interests and skill level',
    sub: "Tell us the subject or skill area, your current level of experience, and what kind of challenge you're looking for.",
    label: 'Your interests and skill level',
    placeholder: "e.g. I'm strong in math and really enjoy olympiad-style problem solving — number theory and combinatorics especially. I've done well in local math club competitions and want to push myself further with regional or national-level contests...",
  },
};

// Kinds that are live (not "coming soon"), in config order.
export const ACTIVE_KINDS = Object.keys(KIND_CONFIG).filter((k) => !KIND_CONFIG[k].comingSoon);
