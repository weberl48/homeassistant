/** Chalk & Turf — mirrors server/app/web/styles.css tokens. Property NAMES
 * keep their old walnut-era spellings (brass/lamp/oxblood) so every
 * downstream style keeps working; the colors they paint changed. */
export const T = {
  ground: "#16382b",
  panel: "#1b4234",
  panel2: "#215040",
  panel3: "#275c4a",
  line: "rgba(242, 247, 239, 0.30)",
  ink: "#f2f7ef",
  inkDim: "#c9dccd",
  inkFaint: "#9fc2ab",
  brass: "#f7c948",
  brassBright: "#ffdd6b",
  brassDeep: "#c29a25",
  lamp: "#58c07c",
  lampBright: "#86e6a8",
  marigold: "#ff9838",
  oxblood: "#ff8177",
  pos: {
    QB: "#ff7a95", RB: "#52d98b", WR: "#6fb1ff",
    TE: "#f0954a", K: "#b591ff", DEF: "#d8d060",
  } as Record<string, string>,
};
