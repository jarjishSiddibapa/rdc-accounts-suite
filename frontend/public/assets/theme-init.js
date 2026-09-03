(function () {
  try {
    var stored = localStorage.getItem('theme')
    var theme = stored === 'dark' || stored === 'light'
      ? stored
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    document.documentElement.setAttribute('data-theme', theme)
  } catch (error) {
    document.documentElement.setAttribute('data-theme', 'light')
  }
})()
