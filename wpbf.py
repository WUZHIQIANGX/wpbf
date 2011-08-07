#!/usr/bin/env python
"""
wpbf is a WordPress BruteForce script to remotely test password strength of the WordPress blogging software

Copyright 2011 Andres Tarantini (atarantini@gmail.com)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import logging, logging.config
import urllib2, urlparse
import sys, threading, Queue, time, argparse

import config, wplib, wptask

class WpbfWorker(threading.Thread):
    """Handle threads that consume the tasks queue"""
    def __init__(self, task_queue):
        threading.Thread.__init__(self)
        self._queue = task_queue

    def run(self):
        while self._queue.qsize() > 0:
            try:
                task = self._queue.get()
                task.run()
                self._queue.task_done()
            except wptask.WpTaskStop:
                self._queue.queue.clear()

if __name__ == '__main__':
    #parse command line arguments
    parser = argparse.ArgumentParser(description='Bruteforce WordPress login form to test password strenght. Currently supports threads, wordlist and basic username detection.')
    parser.add_argument('url', type=str,  help='base URL where WordPress is installed')
    parser.add_argument('-w', '--wordlist', default=config.wordlist, help="worldlist file (default: "+config.wordlist+")")
    parser.add_argument('-nk', '--nokeywords', action="store_false", help="Don't search keywords in content and add them to the wordlist")
    parser.add_argument('-u', '--username', default=config.username, help="username (default: "+config.username+")")
    parser.add_argument('-s', '--scriptpath', default=config.script_path, help="path to the login form (default: "+config.script_path+")")
    parser.add_argument('-t', '--threads', type=int, default=config.threads, help="how many threads the script will spawn (default: "+str(config.threads)+")")
    parser.add_argument('-p', '--proxy', default=None, help="http proxy (ex: http://localhost:8008/)")
    parser.add_argument('-nf', '--nofingerprint', action="store_false", help="Don't fingerprint WordPress")
    parser.add_argument('-eu', '--enumerateusers', action="store_true", help="Only enumerate users (withouth bruteforcing)")
    parser.add_argument('-eut', '--enumeratetolerance', type=int, default=config.eu_gap_tolerance, help="User ID gap tolerance to use in username enumeration (default: "+str(config.eu_gap_tolerance)+")")
    parser.add_argument('-pl', '--pluginscan', action="store_true", help="Detect plugins in WordPress using a list of popular/vulnerable plugins")
    parser.add_argument('--test', action="store_true", help="Run python doctests (you can use a dummy URL here)")
    args = parser.parse_args()
    config.wp_base_url = args.url
    if args.test:
        import doctest
        doctest.testmod(wplib)
        exit(0)
    if args.wordlist:
        config.wordlist = args.wordlist
    if args.username:
        config.username = args.username
    if args.enumeratetolerance:
        config.eu_gap_tolerance = args.enumeratetolerance
    if args.scriptpath:
        config.script_path = args.scriptpath
    if args.threads:
        config.threads = args.threads
    if args.proxy:
        config.proxy = args.proxy
    else:
        proxy = None

    # logger configuration
    logging.config.fileConfig("logging.conf")
    logger = logging.getLogger("wpbf")

    # Wp perform actions over a BlogPress blog
    wp = wplib.Wp(config.wp_base_url, config.script_path, config.proxy)

    # build target url
    logger.info("Target URL: %s", wp.get_base_url())

    # enumerate usernames
    if args.enumerateusers:
        logger.info("Enumerating users...")
        logger.info("Usernames: %s", ", ".join(wp.enumerate_usernames(config.eu_gap_tolerance)))
        exit(0)

    # queue
    task_queue = Queue.Queue()

    # check URL and username
    logger.info("Checking URL & username...")
    usernames = [config.username]
    try:
        if wp.check_username(config.username) is False:
            logger.warning("Possible non existent username: %s", config.username)
            logger.info("Enumerating users...")
            usernames = wp.enumerate_usernames(config.eu_gap_tolerance)
            if len(usernames) > 0:
                logger.info("Usernames: %s", ", ".join(usernames))
            else:
                logger.error("Can't find username :(")
                sys.exit(0)
    except urllib2.HTTPError:
        logger.error("HTTP Error on: %s", wp.get_login_url())
        sys.exit(0)
    except urllib2.URLError:
        logger.error("URL Error on: %s", wp.get_login_url())
        if config.proxy:
            logger.info("Check if proxy is well configured and running")
        sys.exit(0)

    # load fingerprint task into queue
    if args.nofingerprint:
        task_queue.put(wptask.WpTaskFingerprint(config.wp_base_url, config.script_path, config.proxy))

    # check for Login LockDown plugin
    logger.debug("Checking for Login LockDown plugin")
    if wp.check_loginlockdown():
        logger.warning("Login LockDown plugin is active, bruteforce will be useless")
        sys.exit(0)

    # load plugin scan tasks into queue
    if args.pluginscan:
        plugins_list = [plugin.strip() for plugin in open(config.plugins_list, "r").readlines()]
        logger.info("%s plugins will be tested", str(len(plugins_list)))
        for plugin in plugins_list:
            plugin_task = wptask.WpTaskPluginCheck(config.wp_base_url, config.script_path, config.proxy)
            plugin_task.setPluginName(plugin)
            task_queue.put(plugin_task)

    # load wordlist into queue
    wordlist = [config.username]    # add username to the wordlist
    logger.debug("Loading wordlist...")
    try:
        [wordlist.append(w.strip()) for w in open(config.wordlist, "r").readlines()]
    except IOError:
        logger.error("Can't open '%s' the wordlist will not be used!", config.wordlist)
    logger.debug("%s words loaded from %s", str(len(wordlist)), config.wordlist)

    # load into wordlist additional keywords from blog main page
    if args.nokeywords:
        wordlist.append(wplib.filter_domain(urlparse.urlparse(wp.get_base_url()).hostname))     # add domain name to the queue
        [wordlist.append(w.strip()) for w in wp.find_keywords_in_url(config.min_keyword_len, config.min_frequency, config.ignore_with)]

    # load logins into task queue
    logger.info("%s passwords will be tested", str(len(wordlist)*len(usernames)))
    [wordlist.append(u) for u in usernames]
    for username in usernames:
        for password in wordlist:
            login_task = wptask.WpTaskLogin(config.wp_base_url, config.script_path, config.proxy)
            login_task.setUsername(username)
            login_task.setPassword(password)
            task_queue.put(login_task)
    del wordlist

    # start workers
    logger.info("Starting workers...")
    for i in range(config.threads):
        t = WpbfWorker(task_queue)
        t.start()

    # feedback to stdout
    while task_queue.qsize() > 0:
        try:
            # poor ETA implementation
            start_time = time.time()
            start_queue = task_queue.qsize()
            time.sleep(10)
            delta_time = time.time() - start_time
            current_queue = task_queue.qsize()
            delta_queue = start_queue - current_queue
            try:
                wps = delta_time / delta_queue
            except ZeroDivisionError:
                wps = 0.6
            print str(current_queue)+" tasks left / "+str(round(1 / wps, 2))+" tasks per second / "+str( round((wps*current_queue / 60)/60, 2) )+"h left"
        except KeyboardInterrupt:
            logger.info("Clearing queue and killing threads...")
            task_queue.queue.clear()
            for t in threading.enumerate()[1:]:
                t.join()
