import DOMPurify from "dompurify";

const ALLOWED_TAGS = [
  "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
  "span", "a", "img", "ul", "ol", "li", "br", "hr",
  "strong", "em", "b", "i", "u", "s", "sub", "sup",
  "table", "thead", "tbody", "tr", "td", "th", "caption",
  "blockquote", "pre", "code", "figure", "figcaption",
  "section", "article", "aside", "header", "footer", "nav",
  "video", "audio", "source", "picture",
];

const ALLOWED_ATTR = [
  "href", "src", "alt", "class", "style", "id",
  "target", "rel", "width", "height", "loading",
  "controls", "playsinline", "muted", "preload", "poster",
  "colspan", "rowspan", "scope",
];

export function sanitizeHtml(dirty: string): string {
  return DOMPurify.sanitize(dirty, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "input", "style", "link"],
    FORBID_ATTR: ["onerror", "onload", "onclick", "onmouseover", "onfocus", "onblur", "onsubmit"],
  });
}
