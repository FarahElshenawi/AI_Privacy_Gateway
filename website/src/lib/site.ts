// Where the "demo" calls to action point. Set NEXT_PUBLIC_DEMO_URL (booking page or mailto:) when
// there is one; until then the buttons send people to the source code instead of a dead anchor.
export const REPO_URL = "https://github.com/FarahElshenawi/AI_Privacy_Gateway";
export const DEMO_URL = process.env.NEXT_PUBLIC_DEMO_URL || "";
export const CTA_HREF = DEMO_URL || REPO_URL;
export const CTA_LABEL = DEMO_URL ? "Book a demo" : "View the code";
