import { describe, expect, it } from "vitest";
import { AudioPlayer } from "./audioStreamer";

describe("audioStreamer utilities", () => {
  it("initializes AudioPlayer without throwing", () => {
    const player = new AudioPlayer();
    expect(player).toBeDefined();
    player.interrupt();
    player.close();
  });
});
