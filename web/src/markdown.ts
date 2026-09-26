/**
 * markdown.ts — turning the model's markdown into HTML, safely.
 *
 * Two steps, and the second is not optional.
 *
 * The model's reply is NOT trusted input. A customer can write anything into
 * the chat, and the model can be talked into repeating it. If that reached
 * the page unchecked, a reply containing an onerror handler would run their
 * script in our page. DOMPurify removes that while leaving formatting alone.
 *
 * Never write your own HTML sanitiser. It is a problem with a very long tail
 * of edge cases, and a well-tested library is the only sane answer.
 */

import DOMPurify from "dompurify";
import { marked } from "marked";

marked.setOptions({ breaks: true, gfm: true });

export function renderMarkdown(markdown: string): string {
  return DOMPurify.sanitize(marked.parse(markdown, { async: false }) as string);
}
