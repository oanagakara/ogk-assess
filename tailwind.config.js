/** @type {import('tailwindcss').Config} */ 
module.exports = {
  content: [
    "./assessment/templates/**/*.html",
    "./config/templates/**/*.html",
    "./templates/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      "light",
      "dark",
      "corporate",
      {
        business: {
          ...require("daisyui/src/theming/themes")["business"],
          // WCAG AA fix: default error-content (#f2d8d4 on #ac3e31) was 4.469:1,
          // just under the 4.5:1 threshold. Lightened along the same hue to ~4.97:1.
          "error-content": "#fae5e1",
        },
      },
    ],
  },
};
