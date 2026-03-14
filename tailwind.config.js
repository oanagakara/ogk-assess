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
    themes: ["light", "dark", "corporate", "business" ]
  },
};
