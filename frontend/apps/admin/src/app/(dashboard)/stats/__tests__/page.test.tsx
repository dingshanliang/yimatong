import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import StatsRedirect from "../page";

const mockReplace = vi.fn();
const mockGet = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe("StatsRedirect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("replaces the retired stats route with the dashboard homepage", async () => {
    render(<StatsRedirect />);

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/");
    });
    expect(mockGet).not.toHaveBeenCalled();
  });
});
