document.addEventListener("DOMContentLoaded", () => {
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const sidebar = document.querySelector("#sidebar");
  if (menuToggle && sidebar) {
    menuToggle.addEventListener("click", () => sidebar.classList.toggle("sidebar-open"));
    menuToggle.addEventListener("click", () => {
      menuToggle.setAttribute("aria-expanded", String(sidebar.classList.contains("sidebar-open")));
    });
    document.addEventListener("click", (event) => {
      if (window.innerWidth <= 760 && !sidebar.contains(event.target) && !menuToggle.contains(event.target)) {
        sidebar.classList.remove("sidebar-open");
        menuToggle.setAttribute("aria-expanded", "false");
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        sidebar.classList.remove("sidebar-open");
        menuToggle.setAttribute("aria-expanded", "false");
      }
    });
  }

  const roleSelect = document.querySelector("[data-role-select]");
  const doctorFields = document.querySelector("[data-doctor-fields]");
  const doctorInputs = doctorFields ? doctorFields.querySelectorAll("input") : [];
  const updateDoctorFields = () => {
    if (!roleSelect || !doctorFields) return;
    const show = roleSelect.value === "doctor";
    doctorFields.hidden = !show;
    doctorInputs.forEach((input) => { input.required = show; });
  };
  if (roleSelect && doctorFields) {
    roleSelect.addEventListener("change", updateDoctorFields);
    updateDoctorFields();
  }

  document.querySelectorAll("[data-future-date]").forEach((input) => {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    input.min = now.toISOString().slice(0, 16);
  });

  document.querySelectorAll("[data-print]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });
});
