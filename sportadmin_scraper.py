#!/usr/bin/python3

from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from enum import IntEnum, auto
import csv
import sys
import traceback

#
# 1. docs @ https://selenium-python.readthedocs.io
# 2. chrome DevTools, Elements tab, ctrl-f, "Find by string, selector or XPtah
# 3. https://devhints.io/xpath
#

class ReportState(IntEnum):
    PRE_REPORT_AVAILABLE = auto()
    PRE_REPORT_NOT_AVAILABLE = auto()
    PRE_REPORT_NOT_REPORTED = auto()
    CALLED_COMING = auto()
    CALLED_NOT_COMING = auto()
    CALLED_NO_ANSWER = auto()

class SportadminGamesScraper:

    def pretty_print(self, element):
        print(element.get_attribute('outerHTML'))

    def collect(self, email, password):
        self.driver = webdriver.Chrome()

        #
        # LOAD LOGIN PAGE
        #
        self.driver.get("https://identity.sportadmin.se/identity/account/login")
        self.driver.set_window_size(1850, 1053)

        #
        # GO TO PROFILE PAGE
        #
        #self.driver.find_element(By.CSS_SELECTOR, ".btn").click()
        self.driver.find_element(By.ID, "loginemail").send_keys(email)
        self.driver.find_element(By.ID, "loginpass").send_keys(password)
        self.driver.find_element(By.XPATH, "//form/button[@type='submit']").click()

        #
        # SELECT PROFILE: NIKE-LEDARE
        #
        # sometimes this page is not loaded, so check that first.
        if self.driver.current_url == "https://identity.sportadmin.se/profile/user/gateway":
            #self.driver.find_element(By.CSS_SELECTOR, ".userprofile:nth-child(2) .title").click()

            # auto-select the first element
            userprofile = self.driver.find_element(By.CLASS_NAME, "userprofile").click()

        #
        # GO TO "KALLELSER"/ACTIVITIES PAGE WHEN THE DEFAULT PAGE HAS LOADED
        #
        # goto matches page, the page takes time
        # to load due to some "verifierar behörighet" and other
        # activities that are performed. typically this takes
        # less than 20 seconds
        try:
            element = WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.LINK_TEXT, "Kallelser"))
            )
            element.click()
        except TimeoutException:
            traceback.print_exc()
            exit(1)

        #
        # ON ACTIVITIES LIST PAGE
        #
        # Selenium IDE records these actions, haven't investigated the details of why
        # (obviously has to do with iframes)
        self.driver.switch_to.frame(2)
        self.driver.switch_to.frame(0)

        # unselect "show only future matches"
        self.driver.find_element(By.NAME, "visaKommande").click()

        # container for collected data
        data = []

        #page iterator "Nästa sida", mainly used for debugging
        loop = -1
        while True:
            #buttons = self.driver.find_elements(By.XPATH, "//input[@type='button'][@id='butt']")
            activities = self.driver.find_elements(By.ID, "butt")

            # each time we follow a link we will loose the original list of links
            # so simplest way is to remember the index of the current link (because
            # the list of links "should not change") and then recreate the list
            # each time we return to the page after processing one element
            cnt = len(activities)
            print("activities: ", cnt)

            loop += 1
            # offset the parsing on different pages
            # mostly for debugging, but could also be used to limit parsing
            # list contains offset/page
            start = (28, 0)

            #for idx in range(start[loop], cnt):
            for idx in range(cnt):
                #
                # ON ACTIVITIES LIST PAGE
                #
                # regenerate the list of activities
                _activities = self.driver.find_elements(By.ID, "butt")
                if cnt != len(_activities):
                    raise Exception(f'Number of activities changed: {cnt} != {len(_activities)}')
                # get the correct activity
                activity = _activities[idx]

                # keep the selected activity in view
                self.driver.execute_script("arguments[0].scrollIntoView();", activity)

                # click on "Visa" button for the activity
                activity.click()

                #
                # SINGLE ACTIVITY PAGE
                #
                # get a unique activity identifier
                activity_pk = self.driver.find_element(By.XPATH, "//input[@name='aktivitet_pk']").get_attribute('value')

                # click on "Ändra" (activity info) button
                self.driver.find_element(By.XPATH, "//input[@type='button'][@value='Ändra'][not(@id)]").click()

                #
                # SINGLE ACTIVITY DETAILS PAGE
                #
                # find "Aktivitet" row
                row = self.driver.find_element(By.XPATH, "//tr[./td/a = 'Aktivitet']")
                # get second column
                col = row.find_element(By.CSS_SELECTOR, "td:nth-child(2)")
                # get the content of the column
                activity_type = col.text

                # get the activity date
                date_input = self.driver.find_element(By.XPATH, "//input[@name='aktivitet_fran']")
                date = date_input.get_attribute('value')

                # does only exist for matches activity type
                try:
                    # find "Serie:" row
                    row = self.driver.find_element(By.XPATH, "//tr[./td = 'Serie:']")
                    # get second column
                    col = row.find_element(By.CSS_SELECTOR, "td:nth-child(2)")
                    # get the content of the column
                    series = col.text
                except:
                    series = ""

                if ((activity_type != "Match") or
                    (date is None) or
                    (series in ('', 'Lägg till en serie >>'))):
                    # return to ACTIVITIES LIST PAGE
                    #self.driver.find_element(By.XPATH, "//input[@type='button'][@value='Tillbaka']").click()
                    #self.driver.find_element(By.XPATH, "//input[@type='button'][@value='Tillbaka']").click()
                    self.driver.back()
                    self.driver.back()

                    # select the correct iframe
                    self.driver.switch_to.frame(2)
                    self.driver.switch_to.frame(0)
                    continue

                print("%s, %s, %s, %s, " % (activity_type, activity_pk, date, series), end="")

                # return SINGLE ACTIVITY PAGE
                #self.driver.find_element(By.XPATH, "//input[@type='button'][@value='Tillbaka']").click()
                self.driver.back()

                # select the correct iframe
                self.driver.switch_to.frame(2)
                self.driver.switch_to.frame(0)

                # track the aggregates for the activity. We'll compare them later with Sportadmin's
                # own aggregates in the activities list to verify correct parsing
                summary = []

                # tabs: Kommer, Kommer ej, Ej svarat
                for tab_name, state in zip(('kommer', 'kommerEj', 'ejSvarat'), (ReportState.CALLED_COMING, ReportState.CALLED_NOT_COMING, ReportState.CALLED_NO_ANSWER)):
                    tab = self.driver.find_element(By.XPATH, "//li[contains(@onclick,'kallelseFlikar=%s')]" % tab_name)
                    tab.click()

                    # players and trainers are separated by a thin line, find that line and
                    # select all players before the line
                    # i.e. find all "odd" and "even" rows that preceed the row with td of height=2,
                    players = self.driver.find_elements(By.XPATH, "//tr[td[@height=2]]/preceding-sibling::tr[@class='odd' or @class='even']")
                    if not players:
                        # either no players or no trainers (because then the separator line we try to find above does not exist)
                        players = self.driver.find_elements(By.XPATH, "//tr[@class='odd' or @class='even']")
                    # but this also includes the header row, which we don't want
                    players = players[1:]

                    for player in players:
                        name  = player.find_element(By.XPATH, ".//td[3]").text
                        data.append([date, activity_pk, series, name, state])
                    summary.append(len(players))

                # tab: Ej kallad
                tab = self.driver.find_element(By.XPATH, "//li[contains(@onclick,'kallelseFlikar')][4]")
                tab.click()

                #
                # Using preceding-sibling and following-sibling in the initial selector inverts the selection behavior, e.g.
                # "//tr[preceding-sibling::tr[td="FÖRHANDSRAPPORTERAT: Tillgänglig"] and following-sibling::tr[td="EJ FÖRHANDSRAPPORTERAT"]]"
                # selects all tr's between "FÖRHANDSRAPPORTERAT: Tillgänglig" and "EJ FÖRHANDSRAPPORTERAT"
                #

                # collect "ej kallad", "förhandsrapporterad: tillgänglig"
                players = self.driver.find_elements(By.XPATH, "//tr[preceding-sibling::tr[td='%s'] and following-sibling::tr[td='%s']]" % ("FÖRHANDSRAPPORTERAT: Tillgänglig", "EJ FÖRHANDSRAPPORTERAT"))
                for player in players:
                    name  = player.find_element(By.XPATH, ".//td[3]").text
                    data.append([date, activity_pk, series, name, ReportState.PRE_REPORT_AVAILABLE])
                summary.append(len(players))

                # collect "ej kallad", "ej förhandsrapporterad"
                players = self.driver.find_elements(By.XPATH, "//tr[preceding-sibling::tr[td='%s'] and following-sibling::tr[td='%s']]" % ("EJ FÖRHANDSRAPPORTERAT", "FÖRHANDSRAPPORTERAT: Ej tillgänglig"))
                for player in players:
                    name  = player.find_element(By.XPATH, ".//td[3]").text
                    data.append([date, activity_pk, series, name, ReportState.PRE_REPORT_NOT_AVAILABLE])
                summary[-1] += len(players)

                # collect "ej kallad", förhandsrapporterad: "ej tillgänglig"
                # TODO: create smarter XPath query:
                # Varying end-criteria:
                # * any of all different "LEDARE ..." texts
                # * no "LEDARE ..." text, and only a line separator
                # essentially all siblings from start-criteria until one does not contain "onmouseover" attribute
                players = self.driver.find_elements(By.XPATH, "//tr[td='FÖRHANDSRAPPORTERAT: Ej tillgänglig']//following-sibling::tr")
                for player, idx in zip(players, range(len(players))):
                    if not player.get_attribute('onmouseover'):
                        players = players[:idx]
                        break

                for player in players:
                    name  = player.find_element(By.XPATH, ".//td[3]").text
                    data.append([date, activity_pk, series, name, ReportState.PRE_REPORT_NOT_REPORTED])
                summary[-1] += len(players)

                print(summary)

                # return to ACTIVITIES LIST PAGE
                # one can not use self.driver.back() because we have lost track of "back"
                # as we navigated through the 4 tabs above.
                self.driver.find_element(By.XPATH, "//input[@type='button'][@value='Tillbaka']").click()

                # now need to select the correct iframe when pressing the "Tillbaka" button
                #self.driver.switch_to.frame(2)
                #self.driver.switch_to.frame(0)

                # verify collected data against activities list summary
                elements = self.driver.find_elements(By.XPATH, "//input[@type='button'][@id='butt'][contains(@onclick,'%s')]/../preceding-sibling::td[@align='center']" % activity_pk)
                if all( not e.text for e in elements) and all( s == 0 for s in summary):
                    # activity list item shows no players,
                    # so no players were called and none have answered a call
                    # consider this a "match struken"
                    pass
                else:
                    if ((summary[0] != int(elements[0].text.split('/')[0])) or
                        (summary[1] != int(elements[1].text.split('/')[0])) or
                        (summary[2] != int(elements[2].text.split('/')[0])) or
                        (summary[3] != int(elements[3].text.split('/')[0]))):
                        raise Exception("Web scraping failed: (%s, %s, %s, %s) != (%s, %s, %s, %s)" % (summary[0], summary[1], summary[2], summary[3],\
                                        elements[0].text.split('/')[0], elements[1].text.split('/')[0], elements[2].text.split('/')[0], elements[3].text.split('/')[0]))

                #input("Press Enter to continue...")

            # check more pages
            try:
                self.driver.find_element(By.XPATH, "//a[text()='Nästa sida ->']").click()
            except:
                #no more pages
                break

        self.driver.quit()

        with open('sportadmin.csv', 'w', newline='') as csvfile:
            writer = csv.writer(csvfile, delimiter=',', quotechar='|', quoting=csv.QUOTE_MINIMAL)
            for player in data:
                writer.writerow(player)


if __name__ == "__main__":
    print("Hello World!")

    if len(sys.argv[1:]) != 2:
        print("%s loginemail password" % sys.argv[0])
        exit(1)

    email = sys.argv[1]
    password = sys.argv[2]

    sp = SportadminGamesScraper()
    sp.collect(email, password)
