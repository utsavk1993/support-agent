// Adds the DOM matchers — toBeInTheDocument, toBeDisabled and the rest —
// so assertions read as statements about the page rather than about nodes.
import "@testing-library/jest-dom/vitest";

import { ReadableStream } from "node:stream/web";
import { TextDecoder, TextEncoder } from "node:util";

// jsdom does not implement the streams API, and real browsers do. Without
// these, code that reads a streamed response cannot be tested at all —
// which is most of what this client does.
//
// They are needed explicitly because the suite runs on `vmThreads`, which
// builds the DOM once per worker rather than once per file. That is much
// faster, but the VM context no longer inherits Node's globals, so anything
// jsdom omits has to be supplied here.
globalThis.ReadableStream ??= ReadableStream as unknown as typeof globalThis.ReadableStream;
globalThis.TextEncoder ??= TextEncoder;
globalThis.TextDecoder ??= TextDecoder as unknown as typeof globalThis.TextDecoder;
