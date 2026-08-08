import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";

import { queryClient } from "../App";
import { server } from "../mocks/server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  queryClient.clear(); // staleTime Infinity would otherwise leak across tests
});
afterAll(() => server.close());
