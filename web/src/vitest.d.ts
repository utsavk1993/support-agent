/**
 * Registers the DOM matchers — toBeInTheDocument, toBeDisabled and the rest
 * — with TypeScript.
 *
 * vitest.setup.ts imports the same module to install them at runtime, but
 * that file lives in the Node project (it reaches for node:stream), so the
 * application project never sees it and the types would be missing here.
 *
 * This declaration sits inside src/, so it is part of the project the tests
 * are checked against.
 */
import "@testing-library/jest-dom/vitest";
