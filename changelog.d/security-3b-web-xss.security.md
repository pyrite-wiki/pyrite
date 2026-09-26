- The web app's markdown-to-HTML conversion, chat citation links, graph
  tooltips, and source/branding link rendering now consistently sanitise or
  escape KB-derived content before it reaches the DOM. The single-page app's
  responses now carry a Content-Security-Policy and `X-Content-Type-Options`.
