import { beforeEach, describe, expect, it } from "vitest";
import { useScreenFields } from "./onScreen";

describe("the fields a screen shows (#1229)", () => {
  beforeEach(() => useScreenFields.setState({ counts: {} }));

  it("counts a path while a field for it is mounted, per instrument", () => {
    const { add } = useScreenFields.getState();
    const a = add("awg", "ch1.frequency");
    const b = add("awg", "ch1.frequency"); // the same setting twice on one screen
    add("scope", "ch1.frequency");
    a();
    expect(Object.keys(useScreenFields.getState().counts).sort()).toEqual(["awg\nch1.frequency", "scope\nch1.frequency"]);
    b();
    expect(Object.keys(useScreenFields.getState().counts)).toEqual(["scope\nch1.frequency"]);
  });
});
