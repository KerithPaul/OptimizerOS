/**
 * Deterministic rule mapping — the single source of truth for turning backend
 * rule IDs into user-friendly findings.
 *
 * The same rule ID always produces the same user-facing title, summary,
 * explanation and recommended action. No LLM is involved. Backend rule IDs,
 * severities and API contracts are unchanged; this layer only controls
 * presentation.
 *
 * Wording follows the "ArchitectOS — SEO / AEO / GEO User-Friendly Audit
 * Finding Specification" and is kept consistent with the knowledge catalog in
 * `knowledge/{seo,aeo,geo}/*.yaml` (the rules' own content/recommendation
 * text).
 */

export interface FindingCopy {
  /** User-friendly finding name, e.g. "Internal Linking Issue". */
  title: string;
  /** Plain-language statement of what was detected. */
  summary: string;
  /** Why it matters, understandable by a normal website owner. */
  whyItMatters: string;
  /** Clear, practical action the user can take. */
  recommendedAction: string;
  /**
   * Interpretive findings use cautious language and must not be presented as
   * absolute facts; deterministic/measureable findings use factual language.
   */
  interpretive?: boolean;
}

export const FINDING_COPY: Record<string, FindingCopy> = {
  /* SEO ------------------------------------------------------------------- */

  "SEO-BROKEN-INTERNAL-LINK-001": {
    title: "Broken Internal Link",
    summary: "A link on this page points to an internal URL that returned an error.",
    whyItMatters:
      "Internal links help visitors navigate your website and help search systems discover relationships between pages. A link that leads to an error page wastes that path and frustrates visitors.",
    recommendedAction:
      "Fix the link to point at the correct live URL, or remove it if the target page has been intentionally retired.",
  },

  "SEO-CANONICAL-001": {
    title: "Missing Canonical URL",
    summary: "This page does not declare a canonical URL.",
    whyItMatters:
      "A canonical URL tells search systems which address of a page you prefer. Without one, that choice is made for you and may vary.",
    recommendedAction:
      "Add a self-referencing canonical link in the page's head pointing at the page's own preferred URL.",
  },

  "SEO-CANONICAL-CROSSDOMAIN-001": {
    title: "Canonical Points to Another Website",
    summary: "This page declares a canonical URL on a different domain.",
    whyItMatters:
      "A canonical URL pointing at another website can tell search systems to credit that site instead of yours. This is usually a templating mistake, though it is occasionally intentional (for example, syndicated content).",
    recommendedAction:
      "Confirm whether the cross-domain canonical is intentional. If it is not, correct the canonical to point at this site's own preferred URL.",
  },

  "SEO-CONTENT-DUPLICATE-001": {
    title: "Duplicate Content",
    summary: "Two or more crawled pages have identical content.",
    whyItMatters:
      "Identical pages compete with each other, and search systems may pick a different page than the one you would prefer to show.",
    recommendedAction:
      "Consolidate the duplicate pages with a canonical tag or a redirect, or differentiate their content if both are meant to stand independently.",
  },

  "SEO-CONTENT-INTENT-001": {
    title: "Content May Not Match the Page Topic",
    summary:
      "The audit indicates that the page body may not directly address the topic its title and headings promise.",
    whyItMatters:
      "Visitors and search systems expect a page to deliver on what its title and headings promise. When the body only touches on the topic, the page is less satisfying and less likely to perform well.",
    recommendedAction:
      "Review the page and consider reworking the body so it directly and substantively addresses the topic promised by its title and headings.",
    interpretive: true,
  },

  "SEO-CONTENT-THIN-001": {
    title: "Thin Content",
    summary: "This page has very little visible text.",
    whyItMatters:
      "Pages with very little text may leave visitors without the information they came for. Note that some page types (tools, galleries, contact pages) are expected to have few words.",
    recommendedAction:
      "Review whether the page needs more useful content, or whether a low word count is expected for this page type and not a problem.",
  },

  "SEO-H1-MISSING-001": {
    title: "Missing H1 Heading",
    summary: "This page has no main (H1) heading.",
    whyItMatters:
      "The H1 heading names the page's main topic and gives visitors — including people using screen readers — a clear entry point into the page structure.",
    recommendedAction:
      "Add a single, descriptive H1 heading that names the page's main topic, placed before the page's other headings.",
  },

  "SEO-H1-MULTIPLE-001": {
    title: "Multiple H1 Headings",
    summary: "This page has more than one main (H1) heading.",
    whyItMatters:
      "Several top-level headings usually signal that a page covers more than one topic, which can make the page structure harder to follow.",
    recommendedAction:
      "Review whether the page genuinely covers multiple independent topics, or whether the headings should be consolidated under one H1 with the rest demoted to H2 and below.",
  },

  "SEO-HEADING-SKIP-001": {
    title: "Skipped Heading Level",
    summary: "This page skips a heading level, for example going from an H2 directly to an H4.",
    whyItMatters:
      "Headings form the outline of your page. Skipped levels make that outline ambiguous, especially for visitors using screen readers to jump between sections.",
    recommendedAction:
      "Reorder the heading levels so each step down the outline increases by exactly one (for example, insert the missing H3) without changing the visible layout.",
  },

  "SEO-HREFLANG-NOT-RECIPROCAL-001": {
    title: "Incomplete Hreflang Setup",
    summary:
      "This page lists a language alternate that does not link back to this page.",
    whyItMatters:
      "Language annotations only work when every version lists all the others. A one-directional annotation can be ignored entirely, dropping the language signals for both pages.",
    recommendedAction:
      "Add the missing reciprocal alternate link on the target page so every language version lists itself and every other version.",
  },

  "SEO-IMAGE-ALT-001": {
    title: "Missing Image Alt Text",
    summary: "An image on this page is missing alt text.",
    whyItMatters:
      "Alt text describes an image to visitors who cannot see it and helps search systems understand what the image shows.",
    recommendedAction:
      "Add a short, descriptive alt attribute to each content image. Purely decorative images should use an empty alt attribute deliberately, not omit it.",
  },

  "SEO-IMAGE-DIMENSIONS-001": {
    title: "Missing Image Dimensions",
    summary: "An image on this page has no width or height set.",
    whyItMatters:
      "Without explicit dimensions, the browser cannot reserve space before the image loads, which causes layout shifting as the page renders.",
    recommendedAction:
      "Set explicit width and height attributes on each image, matching the image's aspect ratio.",
  },

  "SEO-KEYWORD-FOCUS-001": {
    title: "Main Topic Not Reflected in Search Snippet Fields",
    summary:
      "The audit indicates that the page's most-used terms do not appear in its title or meta description.",
    whyItMatters:
      "The title and meta description are what searchers see first. When a page is clearly about something those fields never mention, the page may be less likely to surface for that topic.",
    recommendedAction:
      "Consider naming the page's dominant terms in the title or meta description where they genuinely describe the page — work in the one or two terms a searcher would actually type, rather than repeating every term.",
    interpretive: true,
  },

  "SEO-LANG-MISSING-001": {
    title: "Missing Page Language Declaration",
    summary: "This page does not declare its language.",
    whyItMatters:
      "Declaring the page language helps screen readers pronounce content correctly and is part of accessibility standards.",
    recommendedAction:
      "Add a lang attribute to the html element with the correct language tag for the page's primary content (for example lang=\"en\").",
  },

  "SEO-METADESC-DUPLICATE-001": {
    title: "Duplicate Meta Descriptions",
    summary: "Multiple pages share the exact same meta description.",
    whyItMatters:
      "The meta description is your page's pitch in search results. Identical descriptions across pages waste the chance to describe what makes each page different.",
    recommendedAction:
      "Write a distinct meta description per page that names what that page specifically covers.",
  },

  "SEO-METADESC-LENGTH-001": {
    title: "Meta Description Likely Too Long",
    summary:
      "This page's meta description is long enough that search results are likely to cut it off.",
    whyItMatters:
      "When a description is cut off, the unique phrasing at the end never appears in search results.",
    recommendedAction:
      "Shorten the meta description so the most important part comes first, within roughly 160 characters.",
  },

  "SEO-METADESC-MISSING-001": {
    title: "Missing Meta Description",
    summary: "This page has no meta description.",
    whyItMatters:
      "The meta description is your chance to control the summary shown under the page in search results. Without one, a summary is generated for you from page content.",
    recommendedAction:
      "Add a short, accurate and page-specific meta description summarizing the page's most relevant content.",
  },

  "SEO-NOINDEX-UNEXPECTED-001": {
    title: "Unexpected Noindex Directive",
    summary:
      "This page is listed in your sitemap but also carries a noindex directive, so it asks to be indexed and blocked at the same time.",
    whyItMatters:
      "A noindex directive prevents a page from appearing in search results. When it contradicts the sitemap, the page can silently disappear from search until traffic drops.",
    recommendedAction:
      "Confirm whether the noindex directive is intentional. If the page should be indexed, remove noindex. If it should not be, remove it from the sitemap so the two signals stop contradicting each other.",
  },

  "SEO-OG-INCOMPLETE-001": {
    title: "Incomplete Social Preview Markup",
    summary:
      "This page has Open Graph tags but is missing one or more of the required ones.",
    whyItMatters:
      "Open Graph tags control how your page appears when shared in chat apps and social platforms. Missing pieces make previews render incomplete or not at all.",
    recommendedAction:
      "Add the missing Open Graph tags (title, type, image, url) so link previews render with complete information.",
  },

  "SEO-ORPHAN-PAGE-001": {
    title: "Internal Linking Issue",
    summary: "No other pages on your website currently link to this page.",
    whyItMatters:
      "Internal links help visitors navigate your website and help search systems discover relationships between pages.",
    recommendedAction: "Add at least one relevant internal link to this page.",
  },

  "SEO-REDIRECT-CHAIN-001": {
    title: "Redirect Chain",
    summary: "This page redirects through more than one hop before reaching its destination.",
    whyItMatters:
      "Every extra redirect adds delay for visitors and another point where the link can break.",
    recommendedAction:
      "Update the original link or redirect rule to point directly at the final destination, collapsing the chain to a single hop.",
  },

  "SEO-ROBOTS-UNAVAILABLE-001": {
    title: "Robots.txt Could Not Be Read",
    summary: "The site's robots.txt file could not be fetched during the audit.",
    whyItMatters:
      "robots.txt tells crawlers which parts of your site they may access. If it cannot be read, search systems cannot tell whether your site has crawling rules at all.",
    recommendedAction:
      "Verify robots.txt is reachable over HTTPS at the site root without authentication, redirects to another host, or blocking of the crawler.",
  },

  "SEO-SITEMAP-COVERAGE-GAP-001": {
    title: "Page Missing from Sitemap",
    summary: "This page was reached through your site's links but is not in your sitemap.",
    whyItMatters:
      "Your sitemap is how you tell search systems which pages exist. Pages missing from it may be discovered later or less reliably.",
    recommendedAction:
      "Add this URL to the sitemap, or regenerate the sitemap from the site's current routes so it stays in sync with the site.",
  },

  "SEO-SITEMAP-INVALID-001": {
    title: "Invalid Sitemap",
    summary: "A sitemap on this site could not be parsed.",
    whyItMatters:
      "A malformed sitemap cannot be read reliably, which means the URLs listed in it may be invisible to search systems.",
    recommendedAction:
      "Regenerate the sitemap with a validated sitemap generator or fix the malformed XML, and confirm it validates before relying on it again.",
  },

  "SEO-STRUCTUREDDATA-INVALID-001": {
    title: "Invalid Structured Data",
    summary: "A structured data block on this page has a syntax error and cannot be read.",
    whyItMatters:
      "Structured data only helps when it can be parsed. A broken block is worse than none: the intent is there, but no system can read it.",
    recommendedAction:
      "Fix the JSON-LD syntax error (common causes: trailing commas, unescaped quotes, or a missing closing brace) and re-validate the block.",
  },

  "SEO-STRUCTUREDDATA-MISSING-001": {
    title: "Structured Data Opportunity",
    summary: "This page has no structured data.",
    whyItMatters:
      "Structured data describes a page's content in a machine-readable way and can make it eligible for rich results. Its absence is not an error — most pages rank without it.",
    recommendedAction:
      "Evaluate whether a schema.org type (Article, Product, Organization, FAQ, breadcrumb, etc.) genuinely fits this page's content, and add markup for it if so.",
  },

  "SEO-TITLE-DUPLICATE-001": {
    title: "Duplicate Page Titles",
    summary: "Multiple pages share the exact same title.",
    whyItMatters:
      "The title is the first thing a searcher sees. Identical titles make it hard to tell pages apart and weaken each page's own relevance signal.",
    recommendedAction:
      "Give each page a unique, descriptive title that reflects what makes that page's content different.",
  },

  "SEO-TITLE-LENGTH-001": {
    title: "Page Title Likely Too Long",
    summary:
      "This page's title is long enough that search results are likely to shorten it.",
    whyItMatters:
      "When a title is shortened, the words at the end may never be shown to searchers.",
    recommendedAction:
      "Shorten the title to the essential, unique description of the page, putting the most important words first.",
  },

  "SEO-TITLE-MISSING-001": {
    title: "Missing Page Title",
    summary: "This page has no title.",
    whyItMatters:
      "The title is the primary label for the page in search results, browser tabs, bookmarks and social shares. Without one, a title is generated for you and may not match the page.",
    recommendedAction:
      "Add a title element with descriptive, concise text unique to the page. Avoid vague values like \"Home\" or \"Untitled\".",
  },

  "SEO-URL-COMPLEXITY-001": {
    title: "Complex URL Structure",
    summary: "This page's URL is long or carries many parameters.",
    whyItMatters:
      "Simple, readable URLs are easier for people to understand and for search systems to crawl. This is a minor factor overall.",
    recommendedAction:
      "Where practical, prefer a shorter, human-readable path over deep parameter chains.",
  },

  "SEO-VIEWPORT-MISSING-001": {
    title: "Missing Mobile Viewport",
    summary: "This page has no viewport meta tag.",
    whyItMatters:
      "Without a viewport tag, the page renders at desktop width on phones, forcing visitors to pinch-zoom and scroll sideways.",
    recommendedAction:
      "Add a viewport meta tag with content=\"width=device-width, initial-scale=1\" inside the page's head.",
  },

  /* AEO ------------------------------------------------------------------- */

  "AEO-ANSWER-LEAD-001": {
    title: "Answer Formatting Needs Improvement",
    summary:
      "The audit indicates that an answer under a question heading is missing or too long to work as a direct answer.",
    whyItMatters:
      "Answer surfaces favor a concise, self-contained answer placed directly under the question. Long, winding answers bury the response.",
    recommendedAction:
      "Immediately follow each question heading with a concise, self-contained answer before any supporting detail, so the direct answer is easy to extract on its own.",
    interpretive: true,
  },

  "AEO-AUTHORSHIP-DATE-001": {
    title: "Missing Author and Date Markup",
    summary:
      "This page's article structured data does not include author or publication date information.",
    whyItMatters:
      "An explicit, machine-readable byline and date are trust and freshness signals that answer engines can use when deciding whether to cite a passage.",
    recommendedAction:
      "Add author and datePublished (and dateModified, if the page is periodically updated) to the page's Article structured data.",
  },

  "AEO-FAQ-SCHEMA-001": {
    title: "FAQ Structured Data Issue",
    summary:
      "This page presents question-and-answer pairs but does not mark them up as FAQ structured data.",
    whyItMatters:
      "FAQ structured data makes your questions and answers machine-readable for answer engines. Note: this no longer produces a Google rich result, but other AI answer engines consume structured data.",
    recommendedAction:
      "If the page already presents multiple visible question/answer pairs authored by the site, add matching FAQPage structured data so the pairs are also machine-readable.",
  },

  "AEO-QUESTION-HEADING-001": {
    title: "No Question-Style Headings",
    summary: "This page has no headings phrased as questions.",
    whyItMatters:
      "Question-form headings (like \"How do I …?\") give answer engines an obvious question-and-answer pair to lift from your content.",
    recommendedAction:
      "Where the page already answers a question users commonly ask, phrase the relevant heading as that question rather than a generic label.",
  },

  /* GEO ------------------------------------------------------------------- */

  "GEO-AI-CRAWLER-ACCESS-001": {
    title: "AI Crawler Access Restricted",
    summary:
      "The site's robots.txt disallows one or more known AI crawlers.",
    whyItMatters:
      "AI systems can only cite your content if their crawlers may fetch it. A disallow does not affect classic search SEO, but it does decide whether that AI system can see and reference your pages.",
    recommendedAction:
      "Review the disallowed AI bots named in the technical details and confirm each is a deliberate choice rather than an accidental blanket disallow.",
  },

  "GEO-ENTITY-CLARITY-001": {
    title: "Entity Information Needs Improvement",
    summary:
      "The audit indicates that this page gives AI systems little explicit information about who or what it is about.",
    whyItMatters:
      "When a page discusses a business, product, or organization without naming it in machine-readable form, an AI system can quote the text but has a harder time citing who or what it is about.",
    recommendedAction:
      "Consider adding Organization (or Person/LocalBusiness, as applicable) structured data naming the entity the page is about.",
    interpretive: true,
  },

  "GEO-SOURCE-ATTRIBUTION-001": {
    title: "No Source References Found",
    summary:
      "This substantial page includes no references to external sources.",
    whyItMatters:
      "Pages that name where their claims come from are easier for retrieval systems to corroborate — and easier for readers to trust — than pages whose claims stand unattributed.",
    recommendedAction:
      "Where the page makes factual or statistical claims, link to the primary source (a study, official documentation, a dataset) rather than leaving the claim unattributed.",
  },

  "GEO-STRUCTURED-DENSITY-001": {
    title: "Structured Data May Improve AI Readability",
    summary:
      "This long page offers no structured data alongside its prose.",
    whyItMatters:
      "It is a hypothesis, not a proven mechanism, that machine-readable structure makes pages easier for AI systems to extract facts from — but a long page with none offers nothing but raw prose to parse.",
    recommendedAction:
      "Consider adding structured data appropriate to the content (Article, Product, HowTo, Dataset, etc.) so machine consumers have a structured summary alongside the prose.",
    interpretive: true,
  },
};

const UNKNOWN_FINDING_COPY: FindingCopy = {
  title: "Audit Finding",
  summary: "This audit check identified an issue that requires review.",
  whyItMatters:
    "ArchitectOS checks your website against established search and answer-engine guidelines. This particular check flagged something that is worth reviewing.",
  recommendedAction:
    "Open the technical details to see exactly what the audit measured, and review the affected page.",
};

export function getFindingCopy(ruleId: string): FindingCopy {
  return FINDING_COPY[ruleId] ?? UNKNOWN_FINDING_COPY;
}

/* Evidence ----------------------------------------------------------------
 * Evidence lines are derived deterministically from the measured observed
 * value when we recognize its shape, so the card can state a simple fact
 * ("Internal links found: 0") instead of raw JSON. Unknown shapes fall back
 * to a neutral factual line.
 * ----------------------------------------------------------------------- */

function describeList(items: unknown[], singular: string, plural?: string): string {
  const word = items.length === 1 ? singular : (plural ?? `${singular}s`);
  return `${items.length} ${word}`;
}

export function buildEvidenceSummary(ruleId: string, observed: unknown): string | null {
  if (observed == null) return null;
  if (typeof observed === "number") return `Found: ${observed}`;
  if (typeof observed === "string") return observed ? observed : null;
  if (!isRecord(observed)) return null;

  switch (ruleId) {
    case "SEO-ORPHAN-PAGE-001": {
      const n = toCount(observed.inbound_internal_links);
      return n === null ? null : `Internal links found: ${n}`;
    }
    case "SEO-BROKEN-INTERNAL-LINK-001": {
      const href = typeof observed.href === "string" ? observed.href : null;
      return href ? `Link target that failed: ${href}` : null;
    }
    case "SEO-CANONICAL-CROSSDOMAIN-001": {
      const domain = typeof observed.canonical_domain === "string" ? observed.canonical_domain : null;
      return domain ? `Canonical domain: ${domain}` : null;
    }
    case "SEO-CONTENT-DUPLICATE-001":
    case "SEO-TITLE-DUPLICATE-001": {
      const urls = stringList(observed.urls);
      if (!urls) return null;
      return `${describeList(urls, "page", "pages")} share the same content`;
    }
    case "SEO-METADESC-DUPLICATE-001": {
      const urls = stringList(observed.urls);
      if (!urls) return null;
      return `${describeList(urls, "page", "pages")} share the same meta description`;
    }
    case "SEO-CONTENT-THIN-001": {
      const n = toCount(observed.word_count);
      return n === null ? null : `Visible words on page: ${n}`;
    }
    case "SEO-H1-MISSING-001": {
      const levels = countList(observed);
      return levels === null ? null : `H1 headings found: 0`;
    }
    case "SEO-H1-MULTIPLE-001": {
      const texts = stringList(observed);
      return texts ? `H1 headings found: ${texts.length}` : null;
    }
    case "SEO-HEADING-SKIP-001": {
      const levels = countList(observed);
      return levels === null ? null : `Heading levels used: ${levels.join(", ")}`;
    }
    case "SEO-IMAGE-ALT-001": {
      const src = typeof observed.src === "string" ? observed.src : null;
      return src ? `Image missing alt text: ${src}` : null;
    }
    case "SEO-IMAGE-DIMENSIONS-001": {
      const src = typeof observed.src === "string" ? observed.src : null;
      return src ? `Image without dimensions: ${src}` : null;
    }
    case "SEO-KEYWORD-FOCUS-001": {
      const missing = stringList(observed.missing_from_title_and_description);
      return missing ? `Term(s) missing from title and description: ${missing.join(", ")}` : null;
    }
    case "SEO-LANG-MISSING-001":
      return "Language attribute on <html>: not set";
    case "SEO-METADESC-LENGTH-001": {
      const text = typeof observed.meta_description === "string" ? observed.meta_description : null;
      return text ? `Meta description length: ${text.length} characters` : null;
    }
    case "SEO-METADESC-MISSING-001":
      return "Meta description: not set";
    case "SEO-NOINDEX-UNEXPECTED-001": {
      const tokens = stringList(observed.robots_tokens);
      return tokens ? `Robots directive(s) found: ${tokens.join(", ")}` : "Robots directive: noindex";
    }
    case "SEO-OG-INCOMPLETE-001": {
      const missing = stringList(observed.missing);
      return missing ? `Missing Open Graph tag(s): ${missing.join(", ")}` : null;
    }
    case "SEO-REDIRECT-CHAIN-001": {
      const hops = countList(observed);
      return hops === null ? null : `Redirect hops: ${hops.length}`;
    }
    case "SEO-ROBOTS-UNAVAILABLE-001":
      return "robots.txt: could not be fetched";
    case "SEO-SITEMAP-COVERAGE-GAP-001":
      return "Sitemap membership: not listed";
    case "SEO-SITEMAP-INVALID-001": {
      const state = typeof observed.parse_state === "string" ? observed.parse_state : null;
      return state ? `Sitemap parse state: ${state}` : null;
    }
    case "SEO-STRUCTUREDDATA-INVALID-001": {
      const err = typeof observed.parse_error === "string" ? observed.parse_error : null;
      return err ? `Parse error: ${err}` : null;
    }
    case "SEO-STRUCTUREDDATA-MISSING-001":
      return "Structured data blocks found: 0";
    case "SEO-TITLE-LENGTH-001": {
      const text = typeof observed.title === "string" ? observed.title : null;
      return text ? `Title length: ${text.length} characters` : null;
    }
    case "SEO-TITLE-MISSING-001":
      return "Title: not set";
    case "SEO-URL-COMPLEXITY-001": {
      const params = toCount(observed.query_params);
      const segments = toCount(observed.path_segments);
      if (params === null && segments === null) return null;
      const parts: string[] = [];
      if (params !== null) parts.push(`${params} URL parameter${params === 1 ? "" : "s"}`);
      if (segments !== null) parts.push(`${segments} path segment${segments === 1 ? "" : "s"}`);
      return `URL has ${parts.join(" and ")}`;
    }
    case "SEO-VIEWPORT-MISSING-001":
      return "Viewport meta tag: not set";
    case "AEO-ANSWER-LEAD-001": {
      const text = typeof observed.text === "string" ? observed.text : null;
      const words = toCount(observed.answer_words);
      if (text && words !== null) return `Question: "${trim(text, 120)}" · answer length: ${words} words`;
      if (text) return `Question: "${trim(text, 120)}" · answer: not found`;
      return null;
    }
    case "AEO-AUTHORSHIP-DATE-001": {
      const type = typeof observed.type === "string" ? observed.type : null;
      return type ? `Structured data type: ${type} (no author or datePublished)` : null;
    }
    case "AEO-FAQ-SCHEMA-001": {
      const answered = toCount(observed.answered_questions);
      return answered === null ? null : `Visible answered questions found: ${answered}`;
    }
    case "AEO-QUESTION-HEADING-001":
      return "Question-style headings found: 0";
    case "GEO-AI-CRAWLER-ACCESS-001": {
      const bots = observed.ai_bot_disallows;
      if (Array.isArray(bots) && bots.length > 0) {
        return `AI crawler(s) disallowed in robots.txt: ${bots.filter(isNonEmptyString).join(", ") || bots.length}`;
      }
      return toCount(bots) !== null ? `AI crawler(s) disallowed in robots.txt: ${toCount(bots)}` : null;
    }
    case "GEO-ENTITY-CLARITY-001":
      return "Organization/Person markup found: none";
    case "GEO-SOURCE-ATTRIBUTION-001":
      return "External source references found: 0";
    case "GEO-STRUCTURED-DENSITY-001":
      return "Structured data blocks found: 0";
    default:
      return null;
  }
}

/** Factual fallback evidence line when we do not recognize the observed shape. */
export function fallbackEvidenceSummary(observed: unknown): string {
  if (observed == null) return "The audit recorded a measurement for this finding.";
  if (typeof observed === "string") return observed || "The audit recorded a measurement for this finding.";
  if (typeof observed === "number" || typeof observed === "boolean") return `Observed value: ${observed}`;
  if (Array.isArray(observed)) {
    return observed.length === 0
      ? "No matching content was found on the page."
      : `${observed.length} item(s) observed.`;
  }
  if (isRecord(observed)) {
    const keys = Object.keys(observed);
    if (keys.length === 0) return "The audit recorded a measurement for this finding.";
    return `Observed: ${keys.map((key) => `${key} = ${summarize(observed[key])}`).join("; ")}`;
  }
  return "The audit recorded a measurement for this finding.";
}

function summarize(value: unknown): string {
  if (value == null) return "not set";
  if (Array.isArray(value)) return value.length === 0 ? "empty" : `${value.length} item(s)`;
  if (isRecord(value)) return `${Object.keys(value).length} field(s)`;
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function stringList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter(isNonEmptyString);
}

function countList(value: unknown): number[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((item) => typeof item === "number") ? (value as number[]) : null;
}

function trim(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/* Severity + category labels --------------------------------------------- */

export const SEVERITY_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

export function getSeverityLabel(severity: string): string {
  return SEVERITY_LABELS[severity] ?? capitalize(severity);
}

export const CATEGORY_LABELS_COPY: Record<string, string> = {
  technical_seo: "Technical SEO",
  content_seo: "Content SEO",
  aeo: "AEO",
  geo: "GEO",
};

export function getCategoryLabel(category: string): string {
  return CATEGORY_LABELS_COPY[category] ?? capitalize(category.replace(/_/g, " "));
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/* Rule coverage ----------------------------------------------------------- */

/** Every rule ID the mapping covers — used in dev to verify catalog parity. */
export const MAPPED_RULE_IDS: string[] = Object.keys(FINDING_COPY);
