import { describe, expect, it } from "vitest";
import {
  formatAgeSeconds,
  formatInteger,
  formatOnlineStatus,
  formatPercent,
  formatQuorumState,
  formatSeconds,
  formatSystemsOperational,
  shortenAddress,
} from "@/lib/format";

describe("format helpers", () => {
  it("renders em dash for null integer metrics", () => {
    expect(formatInteger(null)).toBe("—");
    expect(formatInteger(undefined)).toBe("—");
    expect(formatInteger(8197534)).toBe("8,197,534");
  });

  it("renders em dash for null timing metrics", () => {
    expect(formatSeconds(null)).toBe("—");
    expect(formatSeconds(5)).toBe("5s");
    expect(formatSeconds(4.49)).toBe("4.5s");
  });

  it("marks unavailable quorum and systems when null", () => {
    expect(formatQuorumState(null)).toBe("Unavailable");
    expect(formatQuorumState(true)).toBe("Quorum met");
    expect(formatQuorumState(false)).toBe("Quorum not met");
    expect(formatSystemsOperational(null, 16)).toBe("Unavailable");
    expect(formatSystemsOperational(16, 16)).toBe("16 / 16");
  });

  it("formats ages, percents, addresses, and online status", () => {
    expect(formatAgeSeconds(12)).toBe("12s");
    expect(formatAgeSeconds(75)).toBe("1m 15s");
    expect(formatPercent(1.5)).toBe("1.5%");
    expect(formatPercent(null)).toBe("—");
    expect(shortenAddress("0x1234567890abcdef1234567890abcdef12345678")).toBe(
      "0x1234…5678",
    );
    expect(formatOnlineStatus(true)).toBe("Online");
    expect(formatOnlineStatus(false)).toBe("Unavailable");
    expect(formatOnlineStatus(null)).toBe("Unavailable");
  });
});
