"""Single source of truth for the golden-set profiles.

Writes eval/golden_profiles.json (drives the matching harness AND the scorecard) and
eval/golden_profiles.csv (the human-readable review copy). A profile is the synthesized
first-person prose the matcher ranks against, in the shape synthesizeProfile() emits:
general paragraphs, then "Passion Project: " / "Research Project: " prefixed paragraphs.
Each also carries the ASSUMED theme-selection value (search_theme / intent / next_steps)
the finder's theme picker would produce, plus the three basics (grade / state / gender).
Scant rows deliberately omit intent/next_steps and often basics — grade only hard-gates
when known.
"""
import csv, json, os

NL = "\n\n"
ROOT = os.path.dirname(os.path.abspath(__file__))


def P(id, persona, category, detail, passion, research, grade, state, gender,
      profile_text, search_theme, intent=None, next_steps=None):
    return dict(id=id, persona=persona, category=category, detail_level=detail,
                has_passion_project=passion, has_research_project=research,
                grade=grade, state=state, gender=gender, profile_text=profile_text,
                search_theme=search_theme, intent=intent, next_steps=next_steps or [])


PROFILES = [
    # ---------------- original 10 ----------------
    P("P01", "CS / AI app builder", "Computer Science", "rich", "yes", "no", "11th grade", "California", "male",
      "I'm a junior in California who's really into computer science, especially building things people actually use. "
      "I teach myself full-stack development, I'm comfortable in Python and TypeScript, and I want to study CS or "
      "human-computer interaction in college. My goal this year is to take a real product from prototype to its first users."
      + NL + "Passion Project: I'm building Adio, a mobile app that helps students with ADHD break assignments into timed "
      "steps. I've shipped a beta to about 40 classmates, I run weekly feedback sessions, and I'm now trying to get to real "
      "adoption and eventually pitch it to get funding or into an accelerator.",
      "Building software products that help students",
      "Take my ADHD-focused app Adio from beta to real adoption and pitch it for funding",
      ["reach real users", "get into an accelerator or pitch competition", "raise funding"]),

    P("P02", "Computational linguistics researcher", "Linguistics / CS", "rich", "yes", "yes", "12th grade", "Massachusetts", "female",
      "I'm a senior fascinated by language and computation. I love the overlap between linguistics, math, and programming, "
      "and I want to keep doing original research in this space. My near-term goal is to publish my work and compete against "
      "strong students in computational-linguistics problem solving."
      + NL + "Research Project: I'm studying grapheme-to-phoneme error rates in Finno-Ugric languages with two friends I met "
      "at a summer camp. We built our own dataset, we're running sequence models on it, and I'm writing up the results and "
      "looking for a venue to present or publish them."
      + NL + "Passion Project: I run a small club at school where we design and solve original puzzle-style linguistics "
      "problems, and I'm assembling them into a practice set for the North American Computational Linguistics Olympiad.",
      "Computational linguistics research and problem-solving",
      "Publish my grapheme-to-phoneme research and compete in linguistics olympiads",
      ["find a venue to publish or present", "train sequence models", "prepare for NACLO"]),

    P("P03", "Debate & policy", "Law / Debate / Political Science", "moderate", "no", "no", "10th grade", "Texas", "nonbinary",
      "I'm a sophomore in Texas and my thing is debate. I do policy and Lincoln-Douglas, I've gone to a couple of state "
      "qualifiers, and I care a lot about how law and public policy actually shape people's lives. I'm thinking about pre-law "
      "or political science eventually."
      + NL + "Outside of debate I follow Supreme Court cases pretty closely and I like writing persuasive essays. I want to "
      "find summer programs and competitions that would sharpen my argumentation and get me around other people who take this "
      "as seriously as I do.",
      "Competitive debate and public policy",
      "Sharpen my argumentation and explore pre-law and political science",
      ["attend a debate summer program", "compete at a higher level", "study law and policy"]),

    P("P04", "Journalism & creative writing", "Journalism / Writing", "moderate", "yes", "no", "11th grade", "New York", "female",
      "I'm a junior in New York who wants to be a journalist. I write for our school paper, I read a lot of long-form "
      "reporting, and I'm interested in how storytelling can hold power accountable. I also write short fiction on the side."
      + NL + "Passion Project: I founded and edit an online magazine covering issues that matter to students in my district. "
      "I manage a team of six writers, we publish every two weeks, and I'd love to grow our readership and get real editorial "
      "mentorship or a spot in a summer journalism program.",
      "Journalism and editorial writing",
      "Grow my student magazine and get real editorial mentorship",
      ["join a summer journalism program", "grow readership", "get mentorship"]),

    P("P05", "Environmental science & activism", "Environmental Science", "rich", "yes", "yes", "11th grade", "Washington", "male",
      "I'm a junior in Washington and I'm passionate about the environment, especially water and local ecosystems. I take AP "
      "Environmental Science and AP Chemistry, and I want to study environmental engineering or ecology. I'm happiest doing "
      "hands-on fieldwork."
      + NL + "Research Project: I've been sampling a creek near my house for a year, testing for nitrates and turbidity after "
      "storms, and correlating the results with upstream development. I'm trying to turn the data into a paper and enter it in "
      "a science fair."
      + NL + "Passion Project: I started a volunteer group that does monthly shoreline cleanups and runs storm-drain "
      "stenciling with the city. I coordinate about 30 volunteers and I want to make it a registered nonprofit.",
      "Environmental science and watershed research",
      "Publish my creek water-quality study and grow my cleanup nonprofit",
      ["enter a science fair", "register a nonprofit", "study environmental engineering"]),

    P("P06", "Business & entrepreneurship", "Business", "moderate", "yes", "no", "12th grade", "Illinois", "female",
      "I'm a senior in Illinois interested in business and entrepreneurship. I like marketing and figuring out what makes a "
      "product actually sell, and I want to study business or economics in college."
      + NL + "Passion Project: I run a small Etsy-style shop selling custom stationery that I designed, and I've turned about "
      "$4,000 in revenue over the last year reinvesting into inventory. I want to learn how to scale it and meet other young "
      "founders — a pitch competition or an accelerator would be perfect.",
      "Entrepreneurship and running a small business",
      "Scale my stationery shop and meet other young founders",
      ["join a pitch competition or accelerator", "learn to scale", "study business or economics"]),

    P("P07", "Pre-med / biology researcher", "Medicine / Biology", "rich", "no", "yes", "12th grade", "Ohio", "male",
      "I'm a senior in Ohio and I want to go into medicine, probably as an MD or MD-PhD. I love molecular biology and "
      "neuroscience, I take AP Bio and AP Chem, and I volunteer at a hospital on weekends. I'm looking for research experience "
      "and mentorship to go deeper."
      + NL + "Research Project: I work in a university lab studying how a particular protein misfolds in early-onset "
      "Alzheimer's. I run Western blots and help with cell culture, and my PI said I might be a co-author on the next paper. "
      "I want to keep building serious wet-lab research skills.",
      "Biomedical research and medicine",
      "Deepen my wet-lab research toward an MD or MD-PhD",
      ["find a research program", "build wet-lab skills", "get mentorship"]),

    P("P08", "Scant — math only", "Mathematics", "scant", "no", "no", "", "", "",
      "I really like math and I'm pretty good at it. I want to do something with it.",
      "Mathematics"),

    P("P09", "Scant — vague helper", "Mixed / undecided", "scant", "no", "no", "9th grade", "", "",
      "I want to help people and maybe do something in medicine or science one day.",
      "Medicine and helping people"),

    P("P10", "Scant — arts", "Art", "scant", "no", "no", "", "", "",
      "I like drawing and painting and want to get better at art.",
      "Art and drawing"),

    # ---------------- new 40 ----------------
    P("P11", "Competitive robotics lead", "Engineering / Robotics", "rich", "yes", "no", "11th grade", "Michigan", "male",
      "I'm a junior in Michigan and I'm all about robotics. I'm the mechanical lead on my school's FIRST Robotics team, I love "
      "CAD and machining, and I want to study mechanical or electrical engineering."
      + NL + "Passion Project: I lead the drivetrain subteam on our FRC robot — I designed a swerve module from scratch this "
      "year, I manage a build calendar for 15 students, and I want to get us to the world championship and pick up real "
      "engineering mentorship along the way.",
      "Competitive robotics and mechanical engineering",
      "Lead my FRC team to the world championship and study mechanical engineering",
      ["compete at regionals and worlds", "learn CAD and controls", "find an engineering summer program"]),

    P("P12", "Exoplanet researcher", "Astronomy", "rich", "no", "yes", "12th grade", "Arizona", "female",
      "I'm a senior in Arizona and I'm obsessed with astronomy and astrophysics. I've done a lot of self-study in physics and "
      "Python, and I want to study astrophysics and eventually do observational research."
      + NL + "Research Project: I analyze TESS light curves to look for exoplanet transit signals, working with an online "
      "mentor. I've written my own reduction pipeline in Python and I want to present my results at a student research "
      "symposium and go deeper with a real mentorship.",
      "Astronomy and astrophysics research",
      "Deepen my exoplanet transit research and study astrophysics",
      ["find a research mentorship", "present at a symposium", "study astrophysics"]),

    P("P13", "Chemistry olympiad hopeful", "Chemistry", "moderate", "no", "no", "11th grade", "New Jersey", "male",
      "I'm a junior in New Jersey and chemistry is my favorite subject by far. I take AP Chem, I like the problem-solving side "
      "of it, and I want to push myself against strong students. My goal is to qualify for the national chemistry olympiad and "
      "find a serious summer chemistry program.",
      "Competitive chemistry and the chemistry olympiad",
      "Qualify for the national chemistry olympiad",
      ["study for the USNCO", "join a chemistry summer program", "compete"]),

    P("P14", "Neuroscience lab researcher", "Neuroscience", "rich", "no", "yes", "12th grade", "Pennsylvania", "nonbinary",
      "I'm a senior in Pennsylvania fascinated by how the brain produces attention and memory. I take AP Psychology and AP "
      "Biology and I want to study neuroscience or cognitive science."
      + NL + "Research Project: I work in a university cognitive-neuroscience lab running EEG sessions on a selective-attention "
      "task and helping with the signal analysis in MATLAB. I want to turn my piece of the project into a poster and keep "
      "building real research skills.",
      "Cognitive neuroscience research",
      "Turn my EEG attention project into a poster and study neuroscience",
      ["present a poster", "learn signal analysis", "find a neuroscience research program"]),

    P("P15", "Public-health organizer", "Public Health", "moderate", "yes", "no", "11th grade", "Georgia", "female",
      "I'm a junior in Georgia interested in public health and how communities stay healthy. I take AP Bio and I volunteer at a "
      "free clinic. I want to study public health or epidemiology."
      + NL + "Passion Project: I started a health-education club that runs vaccine-info and nutrition workshops at community "
      "centers in my city. I've organized about a dozen sessions and I want to make it bigger and learn how real public-health "
      "programs are run.",
      "Public health and community health education",
      "Grow my health-education club and study public health",
      ["find a public-health summer program", "learn program design", "study epidemiology"]),

    P("P16", "Psychology-curious sophomore", "Psychology", "moderate", "no", "no", "10th grade", "Florida", "female",
      "I'm a sophomore in Florida and I've gotten really into psychology — why people behave the way they do, how memory and "
      "bias work. I read a lot of pop-psych books and I'm thinking about studying psychology or cognitive science. I'd like to "
      "find a summer program or competition where I can go deeper than my school offers.",
      "Psychology and human behavior",
      "Explore psychology beyond what my school offers",
      ["find a psychology summer program", "try a research experience"]),

    P("P17", "Student investor", "Business / Economics", "rich", "yes", "no", "12th grade", "New York", "male",
      "I'm a senior in New York and I love economics and investing. I read about markets constantly, I've taken AP Micro and "
      "Macro, and I want to study economics or finance."
      + NL + "Passion Project: I founded my school's investment club and manage a real small portfolio with dues we pool — "
      "we're up modestly and I write a weekly markets memo for 40 members. I want to compete in stock-market and economics "
      "competitions and get around people who do this professionally.",
      "Economics, investing, and financial markets",
      "Grow my investment club and compete in economics and markets competitions",
      ["enter an economics or stock-market competition", "learn valuation", "study economics or finance"]),

    P("P18", "Model UN leader", "Political Science / International Relations", "moderate", "yes", "no", "11th grade", "District of Columbia", "female",
      "I'm a junior in DC and I'm deep into Model UN and international relations. I've won a few gavels and I care about "
      "diplomacy, human rights, and global policy. I want to study international relations or political science."
      + NL + "Passion Project: I'm the secretary-general of my school's own MUN conference — I run committees for 200 delegates, "
      "write the background guides, and recruit the staff. I want to find summer programs and conferences that would sharpen my "
      "diplomacy and policy skills.",
      "Model UN and international relations",
      "Sharpen my diplomacy skills and study international relations",
      ["attend an international-relations summer program", "compete at larger MUN conferences", "study political science"]),

    P("P19", "Oral-history researcher", "History", "rich", "no", "yes", "12th grade", "Virginia", "male",
      "I'm a senior in Virginia and I love history, especially local and social history. I take AP US History and AP Euro and I "
      "want to study history and maybe become a historian or archivist."
      + NL + "Research Project: I'm running an oral-history project documenting the civil-rights era in my county — I've "
      "recorded 20 interviews with older residents, done archival work at the county library, and I'm writing it up. I want to "
      "publish it in a student history journal or present it at a conference.",
      "History research and oral history",
      "Publish my civil-rights oral-history project",
      ["submit to a student history journal", "present at a history conference", "study history"]),

    P("P20", "Philosophy & ethics thinker", "Humanities / Philosophy", "moderate", "no", "no", "11th grade", "Massachusetts", "nonbinary",
      "I'm a junior in Massachusetts and I love philosophy — ethics especially, and questions about AI and what we owe each "
      "other. I read a lot of philosophy on my own and I do our school's ethics bowl. I want to study philosophy or "
      "cognitive science, and I'm looking for summer programs, essay competitions, or a place to develop these ideas seriously.",
      "Philosophy and ethics",
      "Develop my philosophy and ethics thinking beyond ethics bowl",
      ["enter a philosophy essay competition", "attend a philosophy summer program"]),

    P("P21", "Poet & literary editor", "Creative Writing", "rich", "yes", "no", "12th grade", "Oregon", "female",
      "I'm a senior in Oregon and writing is my life — poetry especially, but also lyric essays. I read constantly and I want "
      "to study creative writing."
      + NL + "Passion Project: I've assembled a poetry chapbook and I edit my school's literary magazine, where I run the "
      "submissions process for 30 contributors. I want to get my own work published, win recognition in writing competitions, "
      "and find a serious summer writing workshop.",
      "Creative writing and poetry",
      "Get my poetry published and win writing competitions",
      ["submit to literary magazines and contests", "attend a summer writing workshop", "study creative writing"]),

    P("P22", "Young filmmaker", "Film / Media", "moderate", "yes", "no", "11th grade", "California", "male",
      "I'm a junior in California and I make short films — I love directing and cinematography and I want to study film. I've "
      "taught myself editing and color grading."
      + NL + "Passion Project: I write, shoot, and edit short narrative films and post them on YouTube; my last one got into a "
      "regional student festival. I want to find film programs and festivals where I can learn from real filmmakers and get my "
      "work seen.",
      "Filmmaking and cinematography",
      "Get my short films into festivals and learn from real filmmakers",
      ["submit to film festivals", "attend a filmmaking summer program", "study film"]),

    P("P23", "Composer", "Music", "rich", "yes", "no", "12th grade", "Illinois", "female",
      "I'm a senior in Illinois and I compose music — mostly for small ensembles and choir. I've studied theory seriously and "
      "I play piano and cello. I want to study composition in college."
      + NL + "Passion Project: I write original pieces and I've had two performed by my school orchestra. I'm building a "
      "portfolio and I want to get my work performed more widely, win a young-composers competition, and study with real "
      "composers over the summer.",
      "Music composition",
      "Get my compositions performed and win a young-composers competition",
      ["enter a composition competition", "attend a summer composition program", "build a portfolio"]),

    P("P24", "Indie game developer", "Computer Science / Game Design", "moderate", "yes", "no", "11th grade", "Washington", "male",
      "I'm a junior in Washington and I make games. I code in C# with Unity and I love the mix of programming, art, and design. "
      "I want to study computer science or game design."
      + NL + "Passion Project: I released a small puzzle-platformer on itch.io that a few thousand people have played, and I'm "
      "working on a bigger one. I want to find game-jam competitions and programs where I can level up and meet other developers.",
      "Game design and development",
      "Ship my next indie game and meet other developers",
      ["enter a game jam or competition", "find a game-dev program", "study computer science"]),

    P("P25", "Cybersecurity CTF competitor", "Cybersecurity", "rich", "yes", "no", "12th grade", "Texas", "male",
      "I'm a senior in Texas and I'm into cybersecurity — reverse engineering, web exploitation, the whole CTF world. I want to "
      "study computer science with a security focus."
      + NL + "Passion Project: I captain my school's cyber team and we compete in CTFs like picoCTF and CyberPatriot; I write "
      "up our solutions and teach the younger members. I want to compete at a higher level and find a real security program or "
      "internship.",
      "Cybersecurity and capture-the-flag competitions",
      "Compete in CTFs at a higher level and find a security program",
      ["compete in national CTFs", "find a cybersecurity program or internship", "study computer science"]),

    P("P26", "Applied ML researcher", "Data Science / Machine Learning", "rich", "yes", "yes", "12th grade", "California", "female",
      "I'm a senior in California and I love machine learning and data science. I'm strong in Python and I've worked through a "
      "lot of ML courses. I want to study CS or statistics with an ML focus."
      + NL + "Research Project: I built a model that predicts local air-quality spikes from traffic and weather data, and I'm "
      "writing up the methodology. I want to publish it or enter it in a research competition."
      + NL + "Passion Project: I run a data-science club where we take on real datasets for local nonprofits.",
      "Machine learning and data-science research",
      "Publish my air-quality ML project and enter it in a competition",
      ["enter a research competition", "publish the methodology", "study CS or statistics"]),

    P("P27", "Coral reef researcher", "Marine Biology", "rich", "no", "yes", "11th grade", "Hawaii", "female",
      "I'm a junior in Hawaii and I love the ocean — marine biology especially. I dive, I take AP Bio, and I want to study "
      "marine science or ecology."
      + NL + "Research Project: I monitor coral bleaching at a reef near me, logging transect data monthly and comparing it "
      "with water-temperature records. I want to turn it into a science-fair project and a paper, and find a marine-science "
      "research program.",
      "Marine biology and reef research",
      "Turn my coral-bleaching monitoring into a paper and find a marine-science program",
      ["enter a science fair", "publish my findings", "find a marine-science research program"]),

    P("P28", "Climate policy activist", "Environmental Policy", "moderate", "yes", "no", "10th grade", "Colorado", "nonbinary",
      "I'm a sophomore in Colorado and I care intensely about climate change — the policy and organizing side more than the lab "
      "side. I want to study environmental policy or political science."
      + NL + "Passion Project: I organize a local youth climate group — we've run rallies, met with city council about a "
      "climate action plan, and registered voters. I want to find programs and fellowships that teach policy and advocacy so I "
      "can be more effective.",
      "Climate policy and environmental advocacy",
      "Become a more effective climate organizer and study environmental policy",
      ["find a policy or advocacy fellowship", "learn campaign strategy", "study environmental policy"]),

    P("P29", "Baker & food-science tinkerer", "Culinary / Food Science", "moderate", "yes", "no", "11th grade", "Louisiana", "female",
      "I'm a junior in Louisiana and I love baking and the science behind it — why gluten develops, how fermentation works. I'm "
      "curious about food science as a major."
      + NL + "Passion Project: I run a small weekend baking business selling breads and pastries to neighbors, and I experiment "
      "with recipes like a lab notebook. I want to find culinary or food-science programs and maybe a competition to test "
      "myself.",
      "Baking and food science",
      "Grow my baking business and explore food science",
      ["find a culinary or food-science program", "enter a baking competition"]),

    P("P30", "Aspiring athletic trainer", "Health / Kinesiology", "scant", "no", "no", "11th grade", "Texas", "male",
      "I play football and I'm interested in sports medicine and how the body works. I might want to be an athletic trainer or "
      "physical therapist.",
      "Sports medicine and kinesiology"),

    P("P31", "Hospital volunteer coordinator", "Nursing / Pre-Health", "moderate", "yes", "no", "12th grade", "Ohio", "female",
      "I'm a senior in Ohio and I want to become a nurse. I take health-science classes and I've done a CNA course. I like the "
      "hands-on, patient-facing side of medicine."
      + NL + "Passion Project: I coordinate the teen-volunteer program at my local hospital — I schedule 25 volunteers and run "
      "their orientation. I want to find nursing and pre-health summer programs and get more real clinical exposure.",
      "Nursing and clinical pre-health",
      "Get more clinical exposure and prepare for nursing school",
      ["find a nursing or pre-health program", "get clinical volunteering hours"]),

    P("P32", "Bioinformatics / genetics researcher", "Biotechnology / Genetics", "rich", "no", "yes", "12th grade", "Maryland", "male",
      "I'm a senior in Maryland fascinated by genetics and biotech — CRISPR, sequencing, the computational side. I take AP Bio "
      "and I code in Python and R. I want to study bioengineering or computational biology."
      + NL + "Research Project: I'm doing a bioinformatics project analyzing gene-expression datasets to find markers for a "
      "disease, working from public data with a mentor's guidance. I want to publish it and find a serious biotech research "
      "program.",
      "Genetics and bioinformatics research",
      "Publish my gene-expression project and find a biotech research program",
      ["publish my analysis", "find a biotech research program", "study computational biology"]),

    P("P33", "Rocketry team member", "Aerospace Engineering", "rich", "yes", "no", "11th grade", "Alabama", "male",
      "I'm a junior in Alabama and I'm obsessed with aerospace and rocketry. I take physics and calculus and I want to study "
      "aerospace engineering."
      + NL + "Passion Project: I'm on my school's rocketry team competing in The American Rocketry Challenge — I do the "
      "aerodynamics and recovery-system design, and we run our own test launches. I want to place nationally and find an "
      "aerospace program or internship.",
      "Aerospace engineering and rocketry",
      "Place nationally in rocketry and find an aerospace program",
      ["compete in the rocketry challenge", "find an aerospace program or internship", "study aerospace engineering"]),

    P("P34", "Renewable-energy builder", "Engineering / Renewable Energy", "moderate", "yes", "no", "11th grade", "California", "female",
      "I'm a junior in California interested in renewable energy and electrical engineering. I like building things and I care "
      "about climate solutions on the engineering side."
      + NL + "Passion Project: I designed and built a small solar-charging station for my school's courtyard with a team, and "
      "I'm learning about batteries and power electronics. I want to find engineering programs focused on clean energy and "
      "maybe a competition.",
      "Renewable-energy engineering",
      "Go deeper on clean-energy engineering and find a program",
      ["find a clean-energy engineering program", "enter an engineering competition", "study electrical engineering"]),

    P("P35", "Nonprofit founder", "Social Entrepreneurship", "rich", "yes", "no", "12th grade", "North Carolina", "female",
      "I'm a senior in North Carolina and I care about educational equity. I'm drawn to social entrepreneurship — using an "
      "organization to fix a real problem. I want to study public policy or social impact."
      + NL + "Passion Project: I founded a nonprofit that provides free tutoring and donated laptops to middle schoolers in "
      "under-resourced schools; we have 40 volunteer tutors and I've raised about $10,000 in grants. I want to scale it "
      "sustainably and find a social-impact fellowship or accelerator.",
      "Social entrepreneurship and educational equity",
      "Scale my tutoring nonprofit sustainably and find a social-impact fellowship",
      ["find a social-impact fellowship or accelerator", "learn nonprofit management", "raise more funding"]),

    P("P36", "Mock trial captain", "Law / Mock Trial", "moderate", "yes", "no", "11th grade", "Illinois", "male",
      "I'm a junior in Illinois and I'm into law — mock trial specifically. I love building a case and cross-examining. I want "
      "to study something pre-law, maybe political science."
      + NL + "Passion Project: I captain my school's mock-trial team and we've made it to the state tournament; I coach the "
      "underclassmen on direct and cross. I want to find law-focused summer programs and compete at a higher level.",
      "Mock trial and pre-law",
      "Compete in mock trial at a higher level and explore pre-law",
      ["attend a pre-law summer program", "advance at state and nationals", "study political science"]),

    P("P37", "Competitive programmer", "Computer Science / Competitive Programming", "rich", "no", "no", "12th grade", "Washington", "male",
      "I'm a senior in Washington and I do competitive programming seriously — algorithms, data structures, contest math. I "
      "compete in USACO and I'm at the Gold level pushing for Platinum. I want to study computer science at a strong program. "
      "I'm looking for competitions, camps, and anything that will push my algorithmic skills against the best students.",
      "Competitive programming and algorithms",
      "Reach USACO Platinum and push my algorithmic skills",
      ["train for USACO Platinum", "attend an algorithms camp", "compete internationally"]),

    P("P38", "Sports-analytics blogger", "Statistics / Data", "moderate", "yes", "no", "11th grade", "Massachusetts", "male",
      "I'm a junior in Massachusetts and I love statistics, especially sports analytics. I take AP Stats and I code in R. I want "
      "to study statistics or data science."
      + NL + "Passion Project: I run a sports-analytics blog where I model team performance and player value, and I've built a "
      "small following. I want to enter data-science and statistics competitions and find a program that takes the analytical "
      "side seriously.",
      "Statistics and sports analytics",
      "Enter data-science competitions and go deeper on analytics",
      ["enter a statistics or data competition", "find a data-science program", "study statistics"]),

    P("P39", "Animal-shelter volunteer", "Veterinary / Animal Science", "moderate", "yes", "no", "10th grade", "Kentucky", "female",
      "I'm a sophomore in Kentucky and I love animals — I want to be a veterinarian. I take biology and I've shadowed at a vet "
      "clinic once."
      + NL + "Passion Project: I volunteer every week at an animal shelter, where I help with intake and basic care, and I "
      "organized a supplies drive that collected a truckload of donations. I want to find pre-vet or animal-science programs "
      "and get more real experience.",
      "Veterinary medicine and animal science",
      "Get more pre-vet experience and find an animal-science program",
      ["find a pre-vet or animal-science program", "get clinical animal experience"]),

    P("P40", "Archaeology enthusiast", "Anthropology / Archaeology", "moderate", "no", "no", "11th grade", "New Mexico", "nonbinary",
      "I'm a junior in New Mexico fascinated by archaeology and anthropology — how people lived, material culture, ancient "
      "civilizations. I read a lot about it and I want to study anthropology or archaeology. I'm looking for a summer field "
      "school, a dig, or any program where I could get real hands-on experience instead of just reading.",
      "Archaeology and anthropology",
      "Get hands-on archaeology experience through a field program",
      ["find an archaeology field school or dig", "study anthropology"]),

    P("P41", "Theater actor", "Theater / Drama", "moderate", "yes", "no", "12th grade", "New York", "female",
      "I'm a senior in New York and acting is everything to me. I've played leads in our school productions and done community "
      "theater. I want to study acting, ideally at a conservatory."
      + NL + "Passion Project: I'm building an audition portfolio and I've started a student theater troupe that stages one "
      "original show a year. I want to find serious summer acting intensives and pre-conservatory programs.",
      "Theater and acting",
      "Build my audition portfolio and find a pre-conservatory acting program",
      ["find a summer acting intensive", "prepare conservatory auditions", "study acting"]),

    P("P42", "Scant — dance", "Dance", "scant", "no", "no", "11th grade", "California", "female",
      "I dance ballet and contemporary and I want to keep getting better and maybe do it in college.",
      "Dance"),

    P("P43", "Photographer", "Photography / Visual Arts", "moderate", "yes", "no", "11th grade", "Washington", "male",
      "I'm a junior in Washington and I'm serious about photography — documentary and street work especially. I want to study "
      "photography or visual arts."
      + NL + "Passion Project: I've built a portfolio, had two photos in a local exhibition, and I run a photo account with a "
      "small following. I want to find summer photography programs and competitions to sharpen my eye and get my work seen.",
      "Photography and visual arts",
      "Sharpen my photography and get my work exhibited",
      ["find a photography summer program", "enter photography competitions", "build my portfolio"]),

    P("P44", "Peer-tutoring founder & future teacher", "Education / Teaching", "moderate", "yes", "no", "12th grade", "Minnesota", "female",
      "I'm a senior in Minnesota and I want to be a teacher — I love explaining things and working with younger kids. I'm "
      "interested in education and child development."
      + NL + "Passion Project: I founded a peer-tutoring program at my school that pairs upperclassmen with struggling "
      "freshmen; I train the tutors and track how the students improve. I want to find education-focused programs and learn "
      "how good teaching actually works.",
      "Education and teaching",
      "Grow my peer-tutoring program and explore becoming a teacher",
      ["find an education or teaching program", "learn instructional methods"]),

    P("P45", "Earth-science student", "Geology / Earth Science", "moderate", "no", "no", "11th grade", "Utah", "male",
      "I'm a junior in Utah and I love geology and earth science — rocks, plate tectonics, and the landscapes around me. I hike "
      "and collect minerals. I want to study geology or earth science and I'm looking for a summer field program or a "
      "competition where I can go beyond the classroom.",
      "Geology and earth science",
      "Go beyond the classroom in earth science through a field program",
      ["find a geology field program", "study earth science"]),

    P("P46", "Scant — science", "Mixed / undecided", "scant", "no", "no", "", "", "",
      "I like science, it's my favorite subject.",
      "Science"),

    P("P47", "Scant — money/business", "Business", "scant", "no", "no", "10th grade", "", "",
      "I want to do business and make money someday.",
      "Business"),

    P("P48", "Scant — games/computers", "Computer Science", "scant", "no", "no", "", "", "",
      "I'm into video games and computers.",
      "Computers and video games"),

    P("P49", "Scant — animals", "Veterinary / Animal Science", "scant", "no", "no", "9th grade", "", "",
      "I like helping animals.",
      "Helping animals"),

    P("P50", "Scant — undecided writer/artist", "Mixed / undecided", "scant", "no", "no", "", "", "",
      "I'm not sure what I like yet, maybe writing or art.",
      "Writing or art"),
]


def main():
    # JSON: full source (drives the harness).
    with open(os.path.join(ROOT, "golden_profiles.json"), "w", encoding="utf-8") as f:
        json.dump(PROFILES, f, ensure_ascii=False, indent=2)

    # CSV: human review copy (category shown as "theme").
    fields = ["id", "persona", "theme", "detail_level", "has_passion_project",
              "has_research_project", "grade", "state", "gender", "profile_text"]
    with open(os.path.join(ROOT, "golden_profiles.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for p in PROFILES:
            w.writerow({
                "id": p["id"], "persona": p["persona"], "theme": p["category"],
                "detail_level": p["detail_level"], "has_passion_project": p["has_passion_project"],
                "has_research_project": p["has_research_project"], "grade": p["grade"],
                "state": p["state"], "gender": p["gender"], "profile_text": p["profile_text"],
            })

    # quick distribution report
    from collections import Counter
    print("profiles:", len(PROFILES))
    print("detail:", dict(Counter(p["detail_level"] for p in PROFILES)))
    print("passion=yes:", sum(p["has_passion_project"] == "yes" for p in PROFILES),
          "research=yes:", sum(p["has_research_project"] == "yes" for p in PROFILES))
    print("with neither:", sum(p["has_passion_project"] == "no" and p["has_research_project"] == "no" for p in PROFILES))


if __name__ == "__main__":
    main()
