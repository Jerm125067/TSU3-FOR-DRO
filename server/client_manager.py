# tsuserver3, an Attorney Online server
#
# Copyright (C) 2016 argoneus <argoneuscze@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from server import fantacrypt
from server import logger
from server.exceptions import ClientError, AreaError
from enum import Enum
from server.constants import TargetType

import datetime
import time
import re
import random


class ClientManager:
    class Client:
        def __init__(self, server, transport, user_id, ipid):
            self.transport = transport
            self.hdid = ''
            self.pm_mute = False
            self.id = user_id
            self.char_id = -1
            self.area = server.area_manager.default_area()
            self.server = server
            self.name = ''
            self.fake_name = ''
            self.is_mod = False
            self.is_gm = False
            self.is_dj = True
            self.pos = ''
            self.is_cm = False
            self.evi_list = []
            self.disemvowel = False
            self.remove_h = False
            self.disemconsonant = False
            self.gimp = False
            self.muted_global = False
            self.muted_adverts = False
            self.is_muted = False
            self.is_ooc_muted = False
            self.pm_mute = False
            self.mod_call_time = 0
            self.in_rp = False
            self.ipid = ipid
            self.is_user_auth = False
            self.is_visible = True
            
            self.following = ''
            self.followedby = ''
            
            #music flood-guard stuff
            self.mus_counter = 0
            self.mute_time = 0
            self.mus_change_time = [x * self.server.config['music_change_floodguard']['interval_length'] for x in range(self.server.config['music_change_floodguard']['times_per_interval'])]

        def send_raw_message(self, msg):
            self.transport.write(msg.encode('utf-8'))

        def send_command(self, command, *args):
            if args:
                if command == 'MS':
                    for evi_num in range(len(self.evi_list)):
                        if self.evi_list[evi_num] == args[11]:
                            lst = list(args)
                            lst[11] = evi_num
                            args = tuple(lst)
                            break
                self.send_raw_message('{}#{}#%'.format(command, '#'.join([str(x) for x in args])))
            else:
                self.send_raw_message('{}#%'.format(command))

        def send_host_message(self, msg):
            self.send_command('CT', self.server.config['hostname'], msg)

        def send_motd(self):
            self.send_host_message('=== MOTD ===\r\n{}\r\n============='.format(self.server.config['motd']))

        def is_valid_name(self, name):
            name_ws = name.replace(' ', '')
            if not name_ws or name_ws.isdigit():
                return False
            #for client in self.server.client_manager.clients:
                #print(client.name == name)
                #if client.name == name:
                    #return False
            return True
            
        def disconnect(self):
            self.transport.close()

        def is_staff(self):
            """
            Returns True if logged in as 'any' staff role.
            """
            return self.is_mod or self.is_cm or self.is_gm
        
        def change_character(self, char_id, force=False):
            if not self.server.is_valid_char_id(char_id):
                raise ClientError('Invalid Character ID.')
            if not self.area.is_char_available(char_id):
                if force:
                    for client in self.area.clients:
                        if client.char_id == char_id:
                            client.char_select()
                else:
                    raise ClientError('Character not available.')
            
            if self.char_id < 0 and char_id >= 0: # No longer spectator?
                # Now bound by AFK rules
                self.server.create_task(self, ['as_afk_kick', self.area.afk_delay, self.area.afk_sendto])
            
            old_char = self.get_char_name()
            self.char_id = char_id
            self.pos = ''
            self.send_command('PV', self.id, 'CID', self.char_id)
            logger.log_server('[{}]Changed character from {} to {}.'
                              .format(self.area.id, old_char, self.get_char_name()), self)

        def change_music_cd(self):
            if self.is_mod or self.is_cm:
                return 0
            if self.mute_time:
                if time.time() - self.mute_time < self.server.config['music_change_floodguard']['mute_length']:
                    return self.server.config['music_change_floodguard']['mute_length'] - (time.time() - self.mute_time)
                else:
                    self.mute_time = 0
            times_per_interval = self.server.config['music_change_floodguard']['times_per_interval']
            interval_length = self.server.config['music_change_floodguard']['interval_length']
            if time.time() - self.mus_change_time[(self.mus_counter - times_per_interval + 1) % times_per_interval] < interval_length:
                self.mute_time = time.time()
                return self.server.config['music_change_floodguard']['mute_length']
            self.mus_counter = (self.mus_counter + 1) % times_per_interval
            self.mus_change_time[self.mus_counter] = time.time()
            return 0

        def reload_character(self):
            try:
                self.change_character(self.char_id, True)
            except ClientError:
                raise

        def reload_music_list(self):
            """
            Rebuild the music list so that it only contains the target area's
            reachable areas+music. Useful when moving areas/logging in or out.
            """
            
            self.server.build_music_list_ao2(self.area, self)
            # KEEP THE ASTERISK, unless you want a very weird single area comprised
            # of all areas back to back forming a black hole area of doom and despair
            # that crashes all clients that dare attempt join this area.
            self.send_command('FM', *self.server.music_list_ao2)
            
        def change_area(self, area, override = False):
            if area.lobby_area and not self.is_visible and not self.is_mod and not self.is_cm:
                raise ClientError('Change the music, speak IC, or ask a GM/Mod to reveal yourself. Otherwise, you wont be allowed in the desired area.')
            if area.private_area and not self.is_visible:
                raise ClientError('In accordance to basic decency within the server... The Server Owner has prevented you from sneaking in this private area. Reveal yourself and head in if needed.')          
            if self.area == area:
                raise ClientError('User is already in target area.')
            if area.is_locked and not self.is_mod and not self.is_gm and not (self.ipid in area.invite_list):
                raise ClientError('That area is locked!')
            if area.is_gmlocked and not self.is_mod and not self.is_gm and not (self.ipid in area.invite_list):
                raise ClientError('That area is gm-locked!')
            if area.is_modlocked and not self.is_mod and not (self.ipid in area.invite_list):
                raise ClientError('That area is mod-locked!')
                
            if not (area.name in self.area.reachable_areas or '<ALL>' in self.area.reachable_areas or \
            (self.is_mod or self.is_gm or self.is_cm) or override):
                info = 'Selected area cannot be reached from the current one without authorization. Try one of the following instead: '
                if self.area.reachable_areas == {self.area.name}:
                    info += '\r\n*No areas available.'
                else:
                    try:
                        sorted_areas = sorted(self.area.reachable_areas,key = lambda area_name: self.server.area_manager.get_area_by_name(area_name).id)
                        for area in sorted_areas:
                            if area != self.area.name:
                                info += '\r\n*{}'.format(area)
                    except AreaError: #When would you ever execute this piece of code is beyond me, but meh
                        info += '\r\n<ALL>'
                raise ClientError(info)
            old_area = self.area
            if not area.is_char_available(self.char_id):
                try:
                    new_char_id = area.get_rand_avail_char_id()
                except AreaError:
                    raise ClientError('No available characters in that area.')

                self.change_character(new_char_id)
                self.send_host_message('Character taken, switched to {}.'.format(self.get_char_name()))
            self.server.create_task(self, ['as_afk_kick', area.afk_delay, area.afk_sendto])
            self.area.remove_client(self)
            self.area = area
            area.new_client(self)

            self.send_host_message('Changed area to {}.[{}]'.format(area.name, self.area.status))
            logger.log_server(
                '[{}]Changed area from {} ({}) to {} ({}).'.format(self.get_char_name(), old_area.name, old_area.id,
                                                                   self.area.name, self.area.id), self)
            #logger.log_rp(
            #    '[{}]Changed area from {} ({}) to {} ({}).'.format(self.get_char_name(), old_area.name, old_area.id,
            #                                                       self.area.name, self.area.id), self)
            self.send_command('HP', 1, self.area.hp_def)
            self.send_command('HP', 2, self.area.hp_pro)
            self.send_command('BN', self.area.background)
            self.send_command('LE', *self.area.get_evidence_list(self))
            self.reload_music_list() # Update music list to include new area's reachable areas

            if self.followedby != "":
                self.followedby.follow_area(area)

        def follow_user(self, arg):
            self.following = arg
            arg.followedby = self
            self.send_host_message('Began following at {}'.format(time.asctime(time.localtime(time.time()))))
            if self.area != arg.area:
                self.follow_area(arg.area)

        def unfollow_user(self):
            self.following.followedby = ""
            self.following = ""
            self.send_host_message("Stopped following at {}.".format(time.asctime(time.localtime(time.time()))))

        def follow_area(self, area):
            self.send_host_message('Followed user moved area at {}'.format(time.asctime(time.localtime(time.time()))))
            if self.area == area:
                self.send_host_message('Unable to follow to {}: Already in target area.'.format(area.name))
                return
            if area.is_locked and not self.is_mod and not self.is_gm and not (self.ipid in area.invite_list):
                self.send_host_message('Unable to follow to {}: Area is locked.'.format(area.name))
                return
            if area.is_gmlocked and not self.is_mod and not self.is_gm and not (self.ipid in area.invite_list):
                self.send_host_message('Unable to follow to {}: Area is GM-locked.'.format(area.name))
                return
            if area.is_modlocked and not self.is_mod and not (self.ipid in area.invite_list):
                self.send_host_message('Unable to follow to {}: Area is Mod-Locked.'.format(area.name))
                return
            old_area = self.area
            if not area.is_char_available(self.char_id):
                try:
                    new_char_id = area.get_rand_avail_char_id()
                except AreaError:
                    self.send_host_message('Unable to follow to {}: No available characters.'.format(area.name))
                    return

                self.change_character(new_char_id)
                self.send_host_message('Character taken, switched to {}.'.format(self.get_char_name()))

            self.area.remove_client(self)
            self.area = area
            area.new_client(self)

            self.send_host_message('Changed area to {}.[{}]'.format(area.name, self.area.status))
            logger.log_server(
                '[{}]Changed area from {} ({}) to {} ({}).'.format(self.get_char_name(), old_area.name, old_area.id,
                                                                   self.area.name, self.area.id), self)
            #logger.log_rp(
            #    '[{}]Changed area from {} ({}) to {} ({}).'.format(self.get_char_name(), old_area.name, old_area.id,
            #                                                       self.area.name, self.area.id), self)
            self.send_command('HP', 1, self.area.hp_def)
            self.send_command('HP', 2, self.area.hp_pro)
            self.send_command('BN', self.area.background)
            self.send_command('LE', *self.area.get_evidence_list(self))

        def send_area_list(self):
            msg = '=== Areas ==='
            lock = {True: '[LOCKED]', False: ''}
            for i, area in enumerate(self.server.area_manager.areas):
                owner = 'FREE'
                if area.owned:
                    for client in [x for x in area.clients if x.is_cm]:
                        owner = 'MASTER: {}'.format(client.get_char_name())
                        break
                if area.is_gmlocked or area.is_modlocked or area.is_locked:
                    locked = True
                else:
                    locked = False
                    
                if self.is_staff():
                    num_clients = len(area.clients)
                else:
                    num_clients = len([c for c in area.clients if c.is_visible])
                    
                msg += '\r\nArea {}: {} (users: {}) {}'.format(i, area.name, num_clients, lock[locked])
                if self.area == area:
                    msg += ' [*]'
            self.send_host_message(msg)

        def send_limited_area_list(self):
            msg = '=== Areas ==='
            for i, area in enumerate(self.server.area_manager.areas):
                msg += '\r\nArea {}: {}'.format(i, area.name)
                if self.area == area:
                    msg += ' [*]'
            self.send_host_message(msg)

        def get_area_info(self, area_id, mods):
            info = ''
            try:
                area = self.server.area_manager.get_area_by_id(area_id)
            except AreaError:
                raise
                
            info += '= Area {}: {} =='.format(area.id, area.name)
            sorted_clients = []
            for c in area.clients:
                # Conditions to print out a client in /getarea(s) (must satisfy at least one of four)
                # 1. Client is yourself.
                # 2. You are a staff member.
                # 3. Client is visible.
                # 4. Client is a mod when requiring only mods be printed.
                if (c == self or self.is_staff() or c.is_visible or (mods and c.is_mod)):
                    sorted_clients.append(c)
            sorted_clients = sorted(sorted_clients, key=lambda x: x.get_char_name())
            
            for c in sorted_clients:
                info += '\r\n[{}] {}'.format(c.id, c.get_char_name())
                if not c.is_visible:
                    info += ' (S)'
                if self.is_mod:
                    info += ' ({})'.format(c.ipid)
            return info

        def send_area_info(self, current_area, area_id, mods): 
            #if area_id is -1 then return all areas. If mods is True then return only mods
            info = ''
            if area_id == -1:
                # all areas info
                info = '== Area List =='
                unrestricted_access_area = '<ALL>' in current_area.reachable_areas
                for i in range(len(self.server.area_manager.areas)):
                    if ((unrestricted_access_area or self.server.area_manager.areas[i].name in current_area.reachable_areas) or \
                        (self.is_staff())) and len(self.server.area_manager.areas[i].clients) > 0:
                        info += '\r\n{}'.format(self.get_area_info(i, mods))
            else:
                try:
                    info = self.get_area_info(area_id, mods)
                except AreaError:
                    raise
            self.send_host_message(info)

        def send_area_hdid(self, area_id):
            try:
                info = self.get_area_hdid(area_id)
            except AreaError:
                raise
            self.send_host_message(info)				

        def send_all_area_hdid(self):
            info = '== HDID List =='
            for i in range (len(self.server.area_manager.areas)):
                 if len(self.server.area_manager.areas[i].clients) > 0:
                    info += '\r\n{}'.format(self.get_area_hdid(i))
            self.send_host_message(info)			

        def send_all_area_ip(self):
            info = '== IP List =='
            for i in range (len(self.server.area_manager.areas)):
                 if len(self.server.area_manager.areas[i].clients) > 0:
                    info += '\r\n{}'.format(self.get_area_ip(i))
            self.send_host_message(info)

        def send_done(self):
            avail_char_ids = set(range(len(self.server.char_list))) - set([x.char_id for x in self.area.clients])
            char_list = [-1] * len(self.server.char_list)
            for x in avail_char_ids:
                char_list[x] = 0
            self.send_command('CharsCheck', *char_list)
            self.send_command('HP', 1, self.area.hp_def)
            self.send_command('HP', 2, self.area.hp_pro)
            self.send_command('BN', self.area.background)
            self.send_command('LE', *self.area.get_evidence_list(self))
            self.send_command('MM', 1)
            self.send_command('OPPASS', fantacrypt.fanta_encrypt(self.server.config['guardpass']))
            self.send_command('DONE')

        def char_select(self):
            self.char_id = -1
            self.send_done()

        def auth_mod(self, password):
            if self.is_mod:
                raise ClientError('Already logged in.')
            if password == self.server.config['modpass']:
                self.is_mod = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def auth_cm(self, password):
            if self.is_cm:
                raise ClientError('Already logged in.')
            if password == self.server.config['cmpass']:
                self.is_cm = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def auth_gm(self, password):
            if self.is_gm:
                raise ClientError('Already logged in.')
            if password == self.server.config['gmpass']:
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass1'] and ((datetime.datetime.today().weekday() == 6 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 0 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass2'] and ((datetime.datetime.today().weekday() == 0 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 1 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass3'] and ((datetime.datetime.today().weekday() == 1 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 2 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass4'] and ((datetime.datetime.today().weekday() == 2 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 3 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass5'] and ((datetime.datetime.today().weekday() == 3 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 4 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass6'] and ((datetime.datetime.today().weekday() == 4 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 5 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            elif password == self.server.config['gmpass7'] and ((datetime.datetime.today().weekday() == 5 and datetime.datetime.now().hour > 15) or (datetime.datetime.today().weekday() == 6 and datetime.datetime.now().hour < 15)):
                self.is_gm = True
                self.in_rp = False
            else:
                raise ClientError('Invalid password.')

        def get_ip(self):
            return self.ipid

        def get_ipreal(self):
            return self.transport.get_extra_info('peername')[0]

        def get_char_name(self):
            if self.char_id == -1:
                return self.server.spectator_name
            return self.server.char_list[self.char_id]

        def change_position(self, pos=''):
            if pos not in ('', 'def', 'pro', 'hld', 'hlp', 'jud', 'wit'):
                raise ClientError('Invalid position. Possible values: def, pro, hld, hlp, jud, wit.')
            self.pos = pos

        def set_mod_call_delay(self):
            self.mod_call_time = round(time.time() * 1000.0 + 30000)

        def can_call_mod(self):
            return (time.time() * 1000.0 - self.mod_call_time) > 0

        # noinspection PyMethodMayBeStatic
        def disemvowel_message(self, message):
            message = re.sub("[aeiou]", "", message, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", message)

        # noinspection PyMethodMayBeStatic
        def gimp_message(self, message):
            message = ['ERP IS BAN',
                       'I\'m fucking gimped because I\'m both autistic and a retard!',
                       'HELP ME',
                       'Boy, I sure do love Dia, the best admin, and the cutest!!!!!',
                       'I\'M SEVERELY AUTISTIC!!!!',
                       '[PEES FREELY]',
                       'KILL ME',
                       'I found this place on reddit XD',
                       '(((((case????)))))',
                       'Anyone else a fan of MLP?',
                       'does this server have sans from undertale?',
                       'what does call mod do',
                       'does anyone have a miiverse account?',
                       'Drop me a PM if you want to ERP',
                       'Join my discord server please',
                       'can I have mod pls?',
                       'why is everyone a missingo?',
                       'how 2 change areas?',
                       'does anyone want to check out my tumblr? :3',
                       '19 years of perfection, i don\'t play games to fucking lose',
                       'nah... your taunts are fucking useless... only defeat angers me... by trying to taunt just earns you my pitty',
                       'When do we remove dangits',
                       'MODS STOP GIMPING ME',
                       'Please don\'t say things like ni**er and f**k it\'s very rude and I don\'t like it',
                       'PLAY NORMIES PLS']
            return random.choice(message)

    def __init__(self, server):
        self.clients = set()
        self.server = server
        self.cur_id = [False] * self.server.config['playerlimit']
        self.clients_list = []

    def new_client(self, transport):
        cur_id = 0
        for i in range(self.server.config['playerlimit']):
                if not self.cur_id[i]:
                    cur_id = i
                    break
        c = self.Client(self.server, transport, cur_id, self.server.get_ipid(transport.get_extra_info('peername')[0]))
        self.clients.add(c)
        self.cur_id[cur_id] = True
        self.server.client_tasks[cur_id] = dict()
        return c

    def remove_client(self, client):
        try:
            client.followedby.unfollow_user()
        except AttributeError:
            pass
        self.cur_id[client.id] = False
        for task_id in self.server.client_tasks[client.id].keys(): # Cancel client's pending tasks 
            self.server.get_task(client, [task_id]).cancel()
        self.clients.remove(client)

    def get_targets(self, client, key, value, local = False):
        #possible keys: ip, OOC, id, cname, ipid, hdid
        areas = None
        if local:
            areas = [client.area]
        else:
            areas = client.server.area_manager.areas
        targets = []
        if key == TargetType.ALL:
            for nkey in range(6):
                targets += self.get_targets(client, nkey, value, local)
        for area in areas:
            for client in area.clients:
                if key == TargetType.IP:
                    if value.lower().startswith(client.get_ipreal().lower()):
                        targets.append(client)
                elif key == TargetType.OOC_NAME:
                    if value.lower().startswith(client.name.lower()) and client.name:
                        targets.append(client)
                elif key == TargetType.CHAR_NAME:
                    if value.lower().startswith(client.get_char_name().lower()):
                        targets.append(client)
                elif key == TargetType.ID:
                    if client.id == value:
                        targets.append(client)
                elif key == TargetType.IPID:
                    if client.ipid == value:
                        targets.append(client)
        return targets

    def get_muted_clients(self):
        clients = []
        for client in self.clients:
            if client.is_muted:
                clients.append(client)
        return clients

    def get_ooc_muted_clients(self):
        clients = []
        for client in self.clients:
            if client.is_ooc_muted:
                clients.append(client)
        return clients
