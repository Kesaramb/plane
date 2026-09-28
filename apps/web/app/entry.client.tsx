/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// Must stay the first import. Safari has no requestIdleCallback, and RenderIfVisible
// calls it unguarded for every row that scrolls into view, so without this every work
// item list in Safari throws and falls through to the route error page. The import
// used to live in provider.tsx and was deleted along with the chat support component
// (upstream 1faf06c75), leaving the polyfill file in the tree but in no bundle.
import "@/lib/polyfills";

import { startTransition, StrictMode } from "react";
import { hydrateRoot } from "react-dom/client";
import { HydratedRouter } from "react-router/dom";

startTransition(() => {
  hydrateRoot(
    document,
    <StrictMode>
      <HydratedRouter />
    </StrictMode>
  );
});
