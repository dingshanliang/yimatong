import axios from "axios";
import { describe, expect, it } from "vitest";

import { extractErrorMessage } from "../api";

describe("extractErrorMessage", () => {
  it("returns readable text for FastAPI validation detail arrays", () => {
    const error = new axios.AxiosError("Request failed", "ERR_BAD_REQUEST", undefined, undefined, {
      data: {
        detail: [
          {
            type: "missing",
            loc: ["body", "code"],
            msg: "Field required",
            input: { name: "测试经销商" },
          },
        ],
      },
      status: 422,
      statusText: "Unprocessable Entity",
      headers: {},
      config: { headers: new axios.AxiosHeaders() },
    });

    expect(extractErrorMessage(error, "保存失败")).toBe("Field required");
  });
});
