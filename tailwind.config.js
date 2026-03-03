/** @type {import('tailwindcss').Config} */ 
module.exports = {
  content: [
    "./assessment/templates/**/*.html",
    "./config/templates/**/*.html",
    "./templates/**/*.html",
  ],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
};

