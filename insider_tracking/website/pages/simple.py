"""simple.py - the pages that only need the templates: Portfolios, Alerts, About and the 404 page."""


class SimplePages:
    def build(self, site):
        site.writer.page("portfolios.html", "/portfolios/index.html", page="portfolios")
        site.writer.page("alerts.html", "/alerts/index.html", page="alerts")
        site.writer.page("about.html", "/about/index.html", page="about")
        site.writer.page("404.html", "/404.html", page=None)
