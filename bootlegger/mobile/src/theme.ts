/** Orchard Park Night — mirrors server/app/web/styles.css tokens. Property
 * NAMES keep their old walnut-era spellings (brass/lamp/oxblood) so every
 * downstream style keeps working; the colors they paint changed. */
export const T = {
  ground: "#06122e",
  panel: "#0b1c44",
  panel2: "#112552",
  panel3: "#172e62",
  line: "rgba(236, 242, 255, 0.28)",
  ink: "#f2f6ff",
  inkDim: "#c2cde6",
  inkFaint: "#8499c4",
  brass: "#5b8cff",
  brassBright: "#93b4ff",
  brassDeep: "#2e5fd9",
  lamp: "#3ed98a",
  lampBright: "#7fedb6",
  marigold: "#ffa23d",
  oxblood: "#ff5d66",
  pos: {
    QB: "#ff7a95", RB: "#52d98b", WR: "#59d3f2",
    TE: "#f0954a", K: "#c39bff", DEF: "#e4de6b",
  } as Record<string, string>,
};
