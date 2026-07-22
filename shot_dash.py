#!/usr/bin/env python3
"""Shot Dash — local storyboard review dashboard for film production.
Serves a CSV shot list as a filterable grid, inline frame previews,
and a reference image browser. Zero dependencies beyond Python stdlib.

Usage:
    python shot_dash.py [--port 8090] [--frames-dir /path] [--refs-dir /path] [--csv /path]
"""

import csv
import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 8090
CSV_PATH = "/opt/data/home/projects/the-waif/storyboard_shots.csv"
FRAMES_DIR = "/opt/data/home/projects/the-waif/storyboards_gpt"
REFS_DIR = "/opt/data/home/projects/the-waif/storyboard_reference"
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Fountain scene → location + character lookup (from THE WAIF numbered draft)
SCENE_TEXT = {
    '1': 'INT. MASTER BEDROOM - HOUSE - NIGHT #1#\n_ON BEN_\nEyes snap open... a dream falling away.\nHe assesses the room: four walls and windows with blinds. His wife MARIE sleeps beside him.\nBen stands, pulls on a long sleeve shirt, hiding his tattoos.',
    '2': 'INT. CORRIDOR - HOUSE - NIGHT #2#\nBen eases the door shut. Picks up a baseball bat.\nHe sweeps the house. Checks the locks on the windows. Tests the triple-locks on the doors. Click, click, click.\nThe castle is secure. Middle-class decor buried under the perma-mess of a small child.\nBack at his door. He eases down the bat... observes flashlight* *flickering under the doorway opposite.',
    '3': 'INT. JACK\'S BEDROOM - HOUSE - NIGHT #3#\nBen eases open the door. JACK (6), is sitting on the edge of the bed holding a flashlight.\nBEN\nHey buddy. Whatcha doing?\nJACK\nI wanna show you something.\nBen sits beside him. They are facing the wardrobe mirror.\nJack holds out his palm. Waves it slowly up and down.\nJACK\nIt kept moving... in the mirror, it kept moving. Before you came in.\nBEN\nMaybe you were dreaming.\nJACK\nI wasn\'t dreaming Dad. I wasn\'t.\nCUT TO:\nJack sleeps. Ben lies on top of the covers. He is staring at his reflection in the mirror. They wave at each other.',
    '4': 'INT. JACK\'S BEDROOM - HOUSE - DAY #4#\nMorning light. Ben sleeping beside Jack. He stirs, sensing he\'s been watched by... Marie leaning in the doorway.\nMARIE\nTrouble sleeping again?\nBEN\nThe pair of us.\nMarie stands straight and exits.\nMARIE (O.S.)\nGet up and I\'ll make you coffee.',
    '5': 'INT. KITCHEN - HOUSE - DAY #5#\nMarie is drawing a complete set of equations on a transparent easel in the kitchen.\nBen trudges in, buttoning a shirt.\nHis feet walk toward a toy car with a sharp tail-fin... his foot just misses it. He crouches down and picks it up.\nHe displays it to Marie.\nBEN\nWould not have been a good start to the day.\nHe sets it on the tabletop and moves to the fridge. Opens it. Drinks the dregs of some orange juice. Shuts the fridge and notices the drawings pasted to it.\nCrayon pastels of a dark figure, lording over stick people.\nBEN\nJack drew this?\nMARIE\nTo my knowledge, he is the only user of crayon in the house.\nHer phone buzzes. She checks it.\nMARIE\nOh shit- Lily just canceled. A bereavement.\nBEN\nWe\'ll call the agency-\nMARIE\nThey\'ll say too short notice for a replacement.\nBEN\nSo what do we do?\nMARIE\nI can\'t take him to my tutorials.',
    '6': 'EXT. SUBURBAN HOME - DAY #6#\nWe drift up the driveway as the family emerges. A FORD F-250 SUPER DUTY and a GRAY SUBARU FORESTER are parked.\nBen buckles Jack into the Forester\'s child seat.\nBEN\nYou\'re going to go to work with me today.\nJACK\nFixing cars.\nBEN\nBut I need you to promise to do exactly what I say.\nJACK\nI promise.',
    '7': 'EXT. UPSTATE NEW YORK - DAY #7#\nThe Forester travels southward. Joining the interstate.',
    '8': 'INT. FORESTER (MOVING) - DAY #8#\nBen drives. Marie sits in the passenger seat with her laptop open. She transcribes formulas into LaTeX from a photo of her easel.\nIn the rear is Jack, staring at his reflection in the window. Moving his finger between the glass and his face.\nJACK\nSee?\nBen looks back at him.\nJACK\nI told you I was coming with you.\nBen smiles at him in the rearview, turns eyes back to the road. They approach a BUSY INTERSECTION.\nBen *just* makes the light before it changes.\nMARIE\nHate this intersection. We\'d have been trapped there for five minutes.',
    '9': 'EXT. NEW YORK - DAY #9#\nThe Forester drives down a busy avenue. Shadowed by skyscrapers of glass and steel either side.',
    '10': 'INT. FORESTER (MOVING) - DAY #10#\nJack traces the glass with his finger. Reflections and gleaming buildings sliding past.\nBEN\nYou got anything on this weekend?\nMARIE\nTerm papers.\nBEN\nI was thinking... we could go up to the cabin.\nMARIE\nJack, you want to go to the cabin?\nJACK\nI don\'t care.\nMARIE\nHe doesn\'t care, great. Maybe you could finish clearing out the attic.\nBEN\nDid we check for asbestos?\nMARIE\nThe realtor would have had to declare it. Right?\nBEN\nI\'ll wear a mask.\nJack\'s POV: in the reflection of a building, a dark figure sweeps downwards toward the ground.\nIn a split second:\nThe roof of the car caves in.\nA shockwave of blood and gore.\nBen\'s face is filled with the airbag.\nThe car has stopped. The horn sounds.\nBen is covered in blood. A pair of legs has wedged beside him, terminating in sneakers with red stripes.\nTo his right is Marie... a *thigh bone* is wedged in her arm, still sheathed in bloody denim. Red-striped sneaker on a distended foot.\nShe tries to pull it out... turning... looking back... seeing *something* ...\nHer mouth opens to scream but we hear nothing.\nBen follows her gaze, looking to the back seat to see...\nJack\'s head is bisected, one eye moving in idle reflex.\nIn the rear window... a car slams on the brakes but is too late... REAR-ENDING Jack\'s car and-\nCUT TO:',
    '11': 'INT. MOTEL ROOM - DAY #11#\nTight on Ben, waking. Unshaven, heavy around the eyes.\nHe is alone in a cheap room. Early morning light seeping through the blinds. Two single beds: the one he\'s in and the other hosting an open suitcase.',
    '12': 'INT. BATHROOM - MOTEL ROOM - DAY #12#\nBen shaves with an electric razor. He is wearing a shirt and suit jacket.',
    '13': 'INT. MOTEL ROOM - DAY #13#\nBen finishes buttoning a dress shirt and pulls on a jacket to complete his suit. The TV table is surrounded by beer bottles. He shoves them into a trash bag.',
    '14': 'EXT. MOTEL - DAY #14#\nBen locks his door and carries the trash bag with him.\nThe motel is next to an outdoor pool, which sits covered in autumn leaves. He passes a NEIGHBOR, who is parked outside his room on a deck chair.\nBen dumps the bottles in the dumpster.',
    '15': 'INT. PICKUP (MOVING) - NEW YORK STATE - DAY #15#\nBen drives. He turns on the radio.\nMusic. It cuts in and out. He cranks the dial... tries to fix it. No good, the sound keeps going in and out.\nHe kills it and drives silently.',
    '16': 'EXT. MUNICIPAL COURT BUILDING - DAY #16#\nBen moves up the steps. His gait is heavy and slow. He pushes through glass doors.\nMARIE\'S LAWYER (O.S.)\nIt is her intention to refinance and remove his name from the chain.',
    '17': 'INT. CONFERENCE ROOM - COURT BUILDING - DAY #17#\nTWO LAWYERS, each flanking Ben and Marie across a table. Marie stares down at a table. Ben looks at her.\nBEN\'S LAWYER\nHe was a joint contributor to the mortgage. He deserves the proceeds of a sale or the fair value of his half-\nMARIE\'S LAWYER\nShe can afford the mortgage, he can\'t. Your client hasn\'t worked reliably since the... incident. And his record will be seen by the judge.\nBEN\'S LAWYER\nThat has nothing to do with the conduct of the marriage... we want to proceed to a sale as soon as-\nMARIE\nEnough.\nThe lawyers stop talking.\nMARIE\nCan we... can we talk? Ben? Just the two of us?\nBen looks to his lawyer, who shakes his head.',
    '18': 'INT. CAFETERIA - COURT BUILDING - DAY #18#\nBen and Marie sit opposite each other on canteen benches. Two vending machine coffees in front of them.\nMARIE\nSo what I\'m thinking is five bucks of coffee might save us a few hundred dollars in fees.\nBEN\nSix bucks of coffee.\nShe takes out her wallet and peels three dollars onto the table with one hand. Her other arm has limited mobility.\nBEN\nI thought women got a makeover before signing the papers.\nMARIE\nLike you made an effort.\nBEN\nI shaved.\nBen takes the dollars and folds them into his shirt pocket.\nMARIE\nListen... I can\'t sell the house right now. I can pay your half when we get the settlement money through.\nBEN\nI need the money.\nMARIE\nThen we sell the cabin right away. We take the first low ball and you keep the proceeds until I can even up.\nBEN\nIt still needs work-\nMARIE\nThen we get someone in.\nBEN\nNo, I can do it. Save some money. And I can begin right away.\nMARIE\nIf you\'re happy with that.\nThey sit in silence.\nBEN\nI don\'t know how you can live there.\nMARIE\nIt\'s my home.\nBEN\nThe memories.\nMARIE\nYou want to do this?\nBEN\nThe pain-\nMARIE\nThe pain reminds me of him. I\'ve accepted Jack\'s gone. I\'m blessed he was a part of my life... our life. You can do what you want but... I\'m not going to throw in the towel-\nBEN\nLike you did with us.\nMARIE\nOh fuck you.\nBEN\nThere it is.\nMARIE\nWe had problems, OK? It wasn\'t just... it wasn\'t just what happened.',
    '19': 'INT. CORRIDOR - COURT BUILDING - DAY #19#\nTHRU GLASS: Ben and Marie sign papers inside the conference room, overseen by their lawyers.',
    '20': 'EXT. PARKING LOT - COURT BUILDING - DAY #20#\nBen waits at his pickup. He watches Marie approach, unscrewing a set of keys from her ring.\nBEN\nIs the landline still working?\nMARIE\nAnother month.\nBEN\nI\'ll pack your stuff up as well.\nMARIE\nAnd... what about Jack\'s things? You want to split them?\nBEN\nYou take it all. I\'ll leave everything at your house.\nMARIE\nOK, just... let me know before you stop over.\nHe opens the door and sits up into the cab. Thinks.\nBEN\nYou seeing someone?\nMARIE\nIt\'s not your concern. I mean, sorry- that came out wrong.\nBEN\nCame out pretty clearly.\nHe closes the car door.\nMARIE\nI don\'t know if I like the idea of you going up there on your own.\nBEN\nLike you said. It\'s not your concern. Be well.\nHe rolls up the window. Drives.',
    '21': 'INT. PICKUP (MOVING) - COURT BUILDING - DAY #21#\nREARVIEW: Marie getting smaller, vanishing from sight as he turns onto the road.\nOn Ben; his jaw working.\nSLOW FADE TO:',
    '22': 'EXT. DARK WATER - DAY #22#\nWe pull back from a whirlpool in the dark water.',
    '23': 'EXT. BRIDGE - UPSTATE NEW YORK - DAY #23#\nBen\'s truck crosses a bridge over a fast-flowing river.',
    '24': 'INT. PICKUP (MOVING) - BRIDGE - DAY #24#\nBen glances over the passing rail. Watery abyss below.',
    '25': 'EXT. UPSTATE NEW YORK - DAY #25#\nThe pickup drives down a narrow two-lane road. White pine and spruce, opening up to a vista downhill. Cold lake glistens in low sun.\nIt turns off for an exit.',
    '26': 'EXT. WORN ROAD - DAY #26#\nPickup moving over cracked asphalt. Trees thick enough to choke the sunset.',
    '27': 'EXT. BROKEN BOW - DAY #27#\nPatchwork of private docks, nestled among cattail marshes. A sign for BROKEN BOW that Ben passes.',
    '28': 'INT. PICKUP (MOVING) - BROKEN BOW - DAY #28#\nThe main street of BROKEN BOW rolls past at thirty. Flags hang from shop fronts. Diner. Bank. Tackle shop. A barber sweeping his porch. Brick facades softened by lake winters.',
    '29': 'EXT. PICKUP (MOVING) - FOREST ROAD - DAY #29#\nBen is passing some run-down homes - shacks, really - and patchwork trailers sprawling on the edge of deeper woods.',
    '30': 'EXT. ACCESS ROAD - DAY #30#\nThe pickup squeezes down a narrow forest road, encroached by cedars. They open to reveal...',
    '31': 'EXT. FRONT YARD - CABIN - DAY #31#\nThe cabin. Cedar-siding browned like old pennies. Windows shuttered internally and glass lined with metal bars.\nBen gets out of the pickup. A child\'s double swing draws his eye for a moment.\nHe walks up the porch and unlocks one... two... three locks on the heavy door, pushing it open.\nGloom inside. The thump of _something moving._\nBen keeps still. No follow up.\nHe reaches for a fire poker on the porch.',
    '32': 'INT. LODGE ROOM - CABIN - DAY #32#\nBen steps across creaking floorboards. Poker at his side.\nDusty desk, portable radio, bookshelf. Sagging sofa near a cast-iron woodburner and TV. A master bed. Family portrait on the wall - Ben, Jack and Marie.',
    '33': 'INT. KITCHENETTE - CABIN - DAY #33#\nBen eases open the kitchen door. Trees pressed against the glass. A portable gas stove in the corner, the hot plates layered with mildew.',
    '34': 'INT. LODGE ROOM - CABIN - DAY #34#\nThe bathroom door is closed.\nBen tenses himself, reaches for the handle. Swings the door open and-\nA BIG BAT rushes toward him.\nBen stumbles back. The bat clatters around the room. Ben pushes the front door wide open.\nThe bat screeches past him and flies outside.',
    '35': 'INT. BATHROOM - CABIN - DAY #35#\nTrash bags duct-taped to the broken window. Ben dustpans broken glass into a trash bag.',
    '36': 'INT. KITCHENETTE - CABIN - DAY #36#\nBen tips the glass shards into a swing-top trash can. A swarm of flies emerge. Ben gags. He gets a flashlight and opens the lid... the beam finds maggots swarming a DEAD RAT.',
    '37': 'INT. LODGE ROOM - CABIN - NIGHT #37#\nBen takes down books from the bookshelf to put in the box. Physics and math textbooks.\nHe takes the next book down. Felt cover, a slight volume. Different to the others. No jacket.\nHe opens it, reading the title page:\n>*THE RULES OF QUANTUM IMMORTALITY *\nby Q.R. PRESTON<\nHe scans the pages. No markings. The pages are yellowed but seemingly unread, as if just cut.\nStrange diagrams of branching realities.\nA diptych of images showing A BLACK CAT in a box device, very dead, next to a cat in the same box, very alive.\nMovement to Ben\'s left... a black cat we will call SCHRÖDINGER has scaled the wall and is prowling the windowsill. She presses her back against the glass.\nBen swings a shutter over to block Schrödinger from sight.',
    '38': 'EXT. REAR - CABIN - DAY #38#\nBlades close and snap on a branch. The radio plays blues.\nBen works a large pair of loppers, clearing branches encroaching on the windows.\nAs Ben works, he sees Schrödinger spy him from the foliage.',
    '39': 'INT. LODGE ROOM - CABIN - DAY #39#\nBen packs books into a box. He picks up THE RULES OF QUANTUM IMMORTALITY book to pack. Reconsiders. Sets it aside.\nHe tapes the box shut and looks around for other things to pack. His eyes find the box room door.',
    '40': 'INT. JACK\'S ROOM - CABIN - DAY #40#\nThe door eases open, revealing Ben holding a box.\nBen\'s POV: Jack\'s room. A mausoleum.\nToys and books all untouched. Jack\'s distinctive crayon drawings pasted on the walls.\nBen stands on the threshold. His breathing becomes tighter.\nHe retreats and closes the door behind him.\nWe draw closer to the DARK FIGURE on one of Jack\'s drawings.',
    '41': 'INT. LODGE ROOM - CABIN - DAY #41#\nBen\'s gloved hands pull the fuse switch. The houselights die, leaving the afternoon gloom from outside.\nBen slots a flatbar between floorboards and levers it. The nails give and the board rises, revealing crawl-space below. He sets down a lamp, illuminating the electric wire run.\nBen sets a battery-powered lamp next to the wire.\nWith a utility knife, he cuts open the jacket shallowly, revealing the copper. It is dark and corroded.\nHe tests it with the meter. No voltage.\nHe trims back the copper until he finds a length that isn\'t corroded. With lineman\'s pliers, he severs the wire.\nCUT TO:\nHe pulls the dead cable to take it out. It is caught on something.\nHe pulls harder again...\nNot giving.\nBen sticks his head in the gap, sticking the flashlight in front of him. In the crawl-space, he can see an ORNATE ROSEWOOD BOX is crushing the wire against a wooden support.\nCUT TO:\nThe rosewood box sits on Ben\'s lap. He tries the latch. It won\'t give. He jabs a screwdriver into the corner.\nIt opens. Inside is a STRANGE REVOLVER on velvet backing with empty holes for ammunition.\nBen inspects the gun: matte steel, a sigil-like glyph on the side. He opens the cylinder - heptagonal, with six voids and a sealed seventh chamber.\nCUT TO:\nBen dials a number on the landline. He cradles the phone on his neck and inspects the gun. Dim light from outside and the battery lamp the only illumination.\nMARIE (O.S.)\nHello?\nBEN\nIt\'s me. You got a moment to talk?\nMARIE (O.S.)\nOne second.\nThe phone is muffled, as if she\'s speaking to someone.',
    '42': 'INT. LIVING ROOM - HOUSE - DAY #42#\nMarie closes a door behind her. In the glass paneling we can see someone else moving around.\nMARIE\nWhat is it?\nBEN\nI can call another time-\nMARIE\nNow\'s fine, if it\'s short.\nA gentle tap at the window. Schrödinger paws the glass.\nBEN\nLook, I\'m clearing things out here and I found something. I found a gun.\nMARIE\nThe hell you doing keeping a gun there? You don\'t have a license.\nBen waves at Schrödinger to go away.\nBEN\nIt\'s not mine. I was checking if it was yours-\nMARIE\nYou know I hate guns, you\'re the guy who... oh, it doesn\'t matter. You absolutely *sure *it\'s not yours?\nBEN\nHundred percent. Don\'t think it\'s been here that long. Back window was broke when I got here.\nMARIE\nYou don\'t have a license, and you\'re never getting one with your record, so... I think possession is nine tenths of the law. Maybe just... throw it into the lake or something.\nBEN\nCan\'t. I\'ve handled it. They could get latents. DNA. I don\'t know what the history is.\nMARIE\nScrew it, I\'ll drive up and get it-\nBEN\n_No_... no. I\'ll deal with it.\nBen slaps the window, and Schrödinger jumps away.\nMARIE\nI\'m not happy with you being up there alone with that thing.\nBEN\nIt\'s not your concern-\nMARIE (O.S.)\nIt is my concern. I need you to promise me you\'re getting rid of it. Promise me Ben.\nBEN\nOK.',
    '43': 'INT. PICKUP (MOVING) - WOODS - DAY #43#\nBen drives. The portable radio rides shotgun. He glances at the rosewood box stored in the foot well.',
    '44': 'EXT. MAIN STREET - BROKEN BOW - DAY #44#\nBen locks his car and walks up the street. Rosewood box tucked tightly to his side.\nBOY\nHey mister.\nBen looks to his side. A young teenage BOY stands in the alley between shops.\nBOY\nYou looking to buy something?\nDeeper in the shadows, he can see a man in a baseball cap lurking. This is TERRY. He\'s got hands stuffed in jacket.\nTERRY\nAnswer the boy.\nBen shakes his head, keeps moving, crossing the street.',
    '45': 'EXT. LAST CHANCE SUPPLY - DAY #45#\nBen enters a medium-sized hardware and general store.',
    '46': 'INT. LAST CHANCE SUPPLY - DAY #46#\nCoolers, pegboard tool racks, and a glass gun cabinet. Ben approaches the owner JAMIE and opens the rosewood box.\nBEN\nI\'m looking to sell this.\nJAMIE\nWe don\'t buy used... federal records are a pain in the ass. Can\'t do it.\nBEN\nYou know anywhere I could sell it?\nJAMIE\nYou got a permit?\nBEN\n(a beat too long)\nYeah.\nJAMIE\nThere\'s a gun show, town over in a week. Cash sale, no paperwork.\nUnusual item. May I?\nBen nods. Jamie inspects it, feels the weight.\nJAMIE\nYou got any ammo with it?\nI could sell you a box.\nBen can see Terry idling at a corner outside.\nBEN\nSure.\nJAMIE\nI\'ll go in the back.\nBen grabs a basket and moves through the store. He drops in some fasteners, electrical, crimps.\nA stand for CATFISH CATFOOD. The mascot resembles his unwelcome guest. Ben knocks a can into the basket.\nBen sees a cooler stacked with beers. He rounds the corner, finds RICKY (16) - in a wheelchair, staring up at the TV.\nBen turns and returns to the counter. Jamie has returned with a blue box of ammunition.\nJAMIE\nI saw you looking at the beers. I got something stronger, you want.\nJamie takes out a clear bottle of bootleg alcohol and sets it on the table.\nBEN\nWhat\'s the proof?\nJAMIE\nBrother, it\'s an art, not a science. I\'d guess one-twenty, one-thirty. That sorta range.\nBEN\n(resisting)\nMaybe not today.\nThe register chimes the total. Ben counts out his money.\nJAMIE\nGuess you\'re here with your family.\nBEN\nNo.\nJAMIE\nI\'ve seen you up with them, the summer months, right?\nBen says nothing. The TV suddenly gets loud.\nJAMIE\nRicky, turn that down.\n(It gets louder)\nRICKY! Sorry, friend.\nJamie steps from behind the counter and moves to Ricky. His hands are gripped on the remote and have turned up the TV.\nBen finishes counting out his money and leaves.',
    '47': 'INT. PICKUP (MOVING) - DAY #47#\nBen driving. On the passenger seat is the gun case, and a small blue box of ammunition.',
    '48': 'EXT. SCENIC STOP - DAY #48#\nBen\'s pickup is parked off-road.',
    '49': 'INT. FOREST CLEARING - DAY #49#\nBen arranges tomato cans on a log in a row. Walks twelve feet away from them.\nHe quickdraws the revolver from his waistline, fires six shots in rapid series. Cans rupture in a flash of red.\nBen scoops up the spent cartridges.',
    '50': 'EXT. FRONT YARD - CABIN - DAY #50#\nBen arrives back at the cabin, taking the blaring radio and box of groceries with him.\nSchrödinger is staking out the porch. Ben ignores her as he unlocks the door and carries in his box of groceries.\nCUT TO:\nSchrödinger waiting on the porch. The door opens and Ben sets down a bowl of Catfish Catfood. She eats hungrily.\nBEN\nNow fuck off. You\'re not getting in.',
    '51': 'INT. LODGE ROOM - CABIN - SUNSET #51#\nSchrödinger lies beside the lit stove. She watches Ben as he works. Ben uses a crimp to join new wiring to old.\nCUT TO:\nBen nails in the floorboards.\nCUT TO:\nBen flicks the breakers in the fuse box.\nThe houselights turn on.',
    '52': 'INT. LODGE ROOM - CABIN - NIGHT #52#\nBen turns the deadbolts. Slides the bolt. Pulls the internal wooden shutters. The cabin is a lamp-lit tomb of wood.\nSchrödinger remains in the corner. Some old newspapers are spread around in an improvised litter tray.\nBen sits in the armchair and opens the book. He is looking at the title page again.\n>*THE RULES OF QUANTUM IMMORTALITY *\nby Q.R. PRESTON<\nOn the next page, a strange, serpent-like insignia. Below it an address that reads:\n!>PUBLISHED BY PAIMON PRESS\n1475 SHORE ROAD, NORTH HAVEN, N.Y.<\nBen begins to flick through the pages. As he does so, he glances at the rosewood box sitting next to him.\nA diagram that looks like a bare winter oak drawn in precise black ink. A single point marked EVENT HORIZON, branching into two... branching into four... branching again and again into hairline capillaries.\nAs Ben reads, he begins to drift off.\n> BLACK',
    '53': 'INT. LODGE ROOM - CABIN - NIGHT #53#\nBen wakes in the dark. Schrödinger sleeps on the newspapers.\nAlert, he listens.\nA heavy sound from Jack\'s room.\nBen gets out of bed and tries the light switch. No doing. He finds the battery lamp. A pool of light is cast in front of him as he moves towards Jack\'s room.\nDoor is ajar.\nHe pauses at the threshold... pushes it open.\nA recreation of Jack\'s room from the old house. Spacious and impossibly wide for the cabin. The bed is empty.\nBen steps inside. Everything is exactly as it was. Deeper inside, he can see the street outside.\nBen sits on the bed... looking at the mirror where-\nJACK is beside him in the reflection, waving-',
    '54': 'INT. LODGE ROOM - CABIN - DAY #54#\nBen wakes, screaming. Schrödinger bolts into the shadows.\nBen balls up, holding his stomach, beginning to cry.',
    '55': 'EXT. FRONT YARD - CABIN - DAY #55#\nMorning light parted by branches. The sound of an engine.',
    '56': 'INT. PICKUP (MOVING) - FRONT YARD - DAY #56#\nBen is reversing the pickup.\nREARVIEW: the cabin getting closer.',
    '57': 'INT. ATTIC - CABIN - DAY #57#\nLight from the hatch cuts the dark.\nBen in dust-mask crawls inside with the lamp. Dust streams across the light. Cobwebs hang from wooden struts. Relics of a long-dead owner strewn around.\nBen drags a bundle of newspapers toward the hatch. They slam to the ground below in a plume of dust.\nBen moves toward a cardboard box behind a cross beam. He tries to reach it... can\'t quite touch it. He lies prone and crawls under the crossbeam. Gets a hand to the box, reveals:\n!A RAT\'S FACE.\nBen recoils. The rat scurries to a gap in the roofing, squeezing its body outside.\nBen breathes heavy through the mask.',
    '58': 'INT. LODGE ROOM - CABIN - DAY #58#\nBen sets the cardboard box on the table. He glances at Schrödinger, who lounges on the porch.\nBEN\nWhat good are you?\nInside the box: a NUCLEAR ATTACK PREPAREDNESS KIT. An EMERGENCY MANUAL. Inside: a cartoon family practices duck and cover drills. Covering eyes. Treating wounds.\nNext there are IODINE PILLS. WATER PURIFICATION BARS. A DOSIMETER PEN. FIRST AID PACKET. A small GEIGER COUNTER.\nThree dog tags enameled with names. Enough for a family.',
    '59': 'INT. LODGE ROOM - CABIN - DAY #59#\nBen fishes a small blue rubber ball from behind the armchair. He looks at it and sets it in a cardboard box that is otherwise empty.\nHe looks to the door to Jack\'s room.',
    '60': 'INT. JACK\'S ROOM - CABIN - DAY #60#\nThe door is pushed open by Ben. He comes in with the cardboard box, fast. Breathing hard, all business.\nBen begins to clear things away. Pulling pictures from the wall, arranging them in a pile. Taking clothes from the closet and folding them neatly.\nBen crouches down beneath the bed, pulling out toys.\nHe sees a box. It contains a CASSETTE RECORDER. He scoops up the tapes beside it - language tapes in Spanish.\nOne is in color. The scrawl in a child\'s hand that reads "Story tape" with a picture.\nHe takes out the language tape and replaces it with the story tape. He braces himself... and presses play. A burst of tape hiss... giving way to a few words of Jack.\nJACK (O.S.)\nOnce upon a time, there was a man who lived in a tree...\nBen turns the tape off, dropping it like it\'s something hot.',
    '61': 'EXT. FRONT YARD - CABIN - DAY #61#\nBen comes outside fast. Breathes.\nHis eyes see the swing nearby. Two empty seats.\nHe stomps toward it and grabs the frame. Wrenches. The frame sways but holds. He plants feet and yanks it harder. It remains rooted.\nHe drops to his knees, claws dirt from the base. Fingernails scrape away dirt to reveal concrete supports.\nHe stands and casts the dirt from his hands. Sags onto the swing seat, resting a dirty hand on the chain.\nThe seat next to him empty.',
    '62': 'INT. LAST CHANCE SUPPLY - DAY #62#\nA bottle of moonshine is set on the table by Jamie.\nAcross from him is Ben - he raises two fingers. Jamie sets another bottle down.\nJAMIE\nPlanning a party?\nBen says nothing.',
    '63': 'EXT. LAST CHANCE SUPPLY - DAY #63#\nBen carries the bottles toward his car. He gets in and cracks open the lid. Necks a quick drink.\nIt\'s strong and hits him quickly.\nA Chevrolet with tint windows pulls in, and Ben hides the bottle. He starts the engine.',
    '64': 'INT. CHEVROLET - DAY #64#\nTerry is inside. Watching Ben drive away.',
    '65': 'INT. PICKUP (MOVING) - FOREST ROAD - DAY #65#\nBen drives. He notices the Chevrolet behind him.\nBen takes the next forest turn.',
    '66': 'EXT. FOREST TURN - DAY #66#\nThe pickup turns off into forest road. Chevrolet follows.',
    '67': 'INT. PICKUP (MOVING) - FOREST ROAD - DAY #67#\nBen continues driving. The path is getting darker. No houses around. Maybe he\'s fucked up.\nBen pulls over.\nREARVIEW: the Chevrolet mirrors him, doing the same.\nBen stops.\nHe takes the revolver out of the box and sticks it into his waistband. Reconsiders. Puts gun back in the box again.\nOpens the door, now unarmed.',
    '68': 'EXT. FOREST PATH - DAY #68#\nBen gets out and walks toward the Chevrolet. Terry gets out to join the party.\nTERRY\nIt\'s time you left town, narc-\nTerry swings his fist. Ben parries, and catches his wrist and twists his arm. He slams Terry\'s face against the car.\nTERRY\nOK, OK man, just let go. Let go alright.\nBen jacks the arm higher. Terry screams.\nTERRY\nLet me go man! Let me fucking go!\nBen frisks him with his free hand to make sure he\'s not got a piece. Then he drops him, hard. Terry crumples, nursing his arm joint.\nBen walks back to his car... stops midway. His hand is shaking. He breathes it out and turns around.\nMoves toward Terry who is getting to his feet.\nTERRY\nI said alright, I said let me be-\nBEN\nWhat you got?\nTERRY\nI got nothing man, I got-\nBen grabs Terry by the jacket lapels. Brings him to his face and breathes into it.\nTERRY\nUh... uh... perks, oxys, blues...\nBEN\nI want something harder.\nTERRY\nI got eight-balls... hot-rails right here...\nHe brings a hand out with small bags of powder.\nBEN\nSome powdered-down shit.\nTERRY\nNo man, I made them for a special customer. He can... he can wait. You have it.\nBEN\nGive me two. How much?\nTERRY\nThat\'s a hundred.\nBEN\nHow much?\nTERRY\nFifty. I mean fifty.\nBen peels a fifty from his envelope. Terry catches a glance at the faded Ace of Hearts tattoo on Ben\'s wrist. Reacts.\nBen takes the product and returns to his car.',
    '69': 'INT. PICKUP - DAY #69#\nBen sits back in the car. He reaches for the revolver in the glove box. He watches the Chevrolet...\nThe engine revs... Chevrolet does a U-turn. Drives away.',
    '70': 'EXT. FRONT YARD - CABIN - DAY #70#\nBen trudges up the porch. Sees something.\nA small dead rat, mangled on the ground. He can see Schrödinger across the porch, proud of her kill.',
    '71': 'INT. LODGE ROOM - CABIN - NIGHT #71#\nA light bulb is unscrewed by Ben\'s hand. The filament glows for a second, then dies.\nSchrödinger rests in the corner by the stove, watching. Her eyes are lit up by the flash of the filament.\nCUT TO:\nBen taps the bulb with a screwdriver until the base breaks. He pulls out the screw housing, filament and glass fragments, leaving a hollow glass shell.\nCUT TO:\nBen opens a lantern. He unscrews the burner assembly from the fuel fount counter-clockwise. He lifts it, revealing the narrow steel air tube.',
    '72': 'INT. KITCHENETTE - CABIN - NIGHT #72#\nBen washes the kerosene from the air tube. As he does so, he catches his reflection in the window. He pauses for a moment, then continues cleaning the air tube.',
    '73': 'INT. LODGE ROOM - CABIN - NIGHT #73#\nBen holds a lighter, blackening the bulb glass.\nThe powder darkens and runs. He inserts the air tube and sucks it in. Eyes close.\nIn the corner, Schrödinger watches him carefully. Branches tap the windows insistently.',
    '74': 'INT. JACK\'S ROOM - CABIN - NIGHT #74#\nClose on the tape recorder. Lit by light from the lodge room. Shadowed by Ben as he picks it up.',
    '75': 'INT. LODGE ROOM - CABIN - NIGHT #75#\nBen sets the tape recorder down on the table. He wills himself to press the button.\nSchrödinger stares at him from a corner. Ben scoops her up.',
    '76': 'INT. KITCHENETTE - CABIN - NIGHT #76#\nBen takes Schrödinger to the back door. He undoes the latch and sets it outside.\nHer paws pat against the glass and he closes the shutter.',
    '77': 'INT. LODGE ROOM - CABIN - NIGHT #77#\nBen pours two fingers of moonshine into a glass. Drains it.\nHe rewinds the tape, hits play. His eyes well as he listens.\nJACK (O.S.)\n*-was a man who lived in a tree... and it was big, the biggest tree in the whole forest. And the man lived right at the top where the branches got really thin. And he couldn\'t come down. He\'d been up there so long he thought the tree was the whole world. And, and, and there was a bird, a black bird that came every day and sat on the branch ... it said "you have to go down now"...*\nBen hurls the glass against the wall. It shatters.',
    '78': 'INT. LODGE ROOM - CABIN - NIGHT #78#\nBen is high. His shoes crackle across the broken glass as he goes to pick up the phone. He dials and waits. It is answered.\nBEN\nHey.\nMARIE (O.S.)\nBen?\nBEN\nI\'m calling because... I\'m calling because I packed up his room.\nMARIE (O.S.)\nMust have been tough.\nBEN\nWhat I keep thinking is... what I keep going over again and again... if I had just left the house a few seconds earlier, a few seconds later... if I had stepped on the gas a little, or eased up on it, or tapped the brakes a little more... they wouldn\'t have hit us.\nMARIE (O.S.)\nDon\'t think about it that way.\nBEN\nIt\'s true though, isn\'t it?\nMARIE (O.S.)\nWhat\'s happened, happened. What happens next you have control over. You had a do-over on your life once before. With us. Now you have another...\nBEN\nI can\'t do it all again. I\'m too tired. I\'m just too tired.\nHe coughs heavily.\nMARIE (O.S.)\nAre you getting high?\nBen says nothing. Another voice sounds on the line. Marie covers the phone, then uncovers it.\nMARIE (O.S.)\nListen, I have to go. Let\'s make a time for you to take the stuff here. We can still split-\nHe hangs up... and deliberately sets the phone off the hook.\nBen opens the rosewood box. Takes the gun into his hand. Loads a round from the ammo box, snaps the cylinder shut.\nBen pulls a chair and sits in front of the mirror. He looks down at the chamber with the solitary bullet.\nHe spins it, snaps it shut blind.\nHe\'s calm. No more struggling. Relief.\nHe puts the gun to his head.\nBreathes faster... begins to squeeze the trigger-\n!BANG BANG.\nKnocking at the door. The lights flicker.\nBen\'s gunhand drops and he stands.\nBANG BANG BANG.\nBen moves to the door, gun at his side.\nTHRU THE PEEPHOLE: A woman. Skin and bones. Dirty and dark-eyed, wearing thin, filthy clothes. This is THE WAIF.\nHer hand smacks the peephole, smearing it with mud.\nShe BANGS the door again.\nWAIF (O.S.)\nHello! Is there anyone there?\nBen says nothing.\nWAIF (O.S.)\nPlease! Could you open up!\nBEN\n(finally)\nWhat do you want?\nWAIF (O.S.)\nI can\'t hear you.\nBEN\n(loud)\nWhat Do You Fucking Want?\nWAIF (O.S.)\nI\'m in trouble... I need help, please. Can you help me?\nBEN\nWho\'s out there with you?\nWAIF (O.S.)\nI can\'t hear you-\nBEN\nAre You Alone?\nTHRU PEEPHOLE: rain lashes on Waif. Exposed to the elements.\nWAIF\nWhat does it look like? Yeah I\'m alone.\nBen tilts one of the window shutters. Looks into the dark forest line. Anyone could be there.\nWAIF (O.S.)\nIt\'s so cold out here. So cold...\nBen tucks the gun into his rear waistband. He undoes the locks one by one. Leaves on the chain.\nINT./EXT. CABIN - NIGHT #79#\nBen opens a crevice in the door. Waif is framed in the gap.\nWAIF\nI\'m sorry, I\'m sorry, I\'m in deep trouble mister, I\'m in real trouble, I saw your place- it\'s the only place round here, right? I need some help. Can you help me?\nBen looks over her shoulder, scoping the darkness.\nBEN\nWhere\'s your car?\nWAIF\nNo, no, I don\'t have a car... I tried calling for a cab.\nShe holds out a cheap cracked dumbphone. He looks past it to her mud-covered sneakers. Tattoo coiling up her bare thighs.\nBEN\nNo signal here. You want signal, you need to go back up on the hill-\nWAIF\nI think the battery is dead now as well. Can I just come inside?\nBEN\nYeah, that\'s not gonna happen.\nWAIF\nI\'m getting soaked here-\nHe shuts the door.\nWAIF\nWhat the fuck? Are you serious?',
    '80': 'INT. LODGE ROOM - CABIN - NIGHT #80#\nBen slides the bolt. She BANGS the door again.\nWAIF (O.S.)\nHey, mister. Mister!\nSchrödinger is staring at Ben. Ben ignores her.\nWAIF (O.S.)\nPlease! Can you... can you even call a cab for me! Can you do that? You got a landline here, I can see the line, OK?\nShe now appears at a window. Taps it. Schrödinger jumps up, rubs her back against the glass.\nWAIF\nPlease, I don\'t know what to do.\nBen walks over and closes the shutter.\nWAIF (O.S.)\nListen... I got someone looking for me! OK! I need to get inside somewhere, or just get away from here. Please help me.\nHe opens the shutter.\nBEN\nIf I call a cab, will you fuck off?\nWAIF\nI can\'t hear you, what?\nHe directs her to the door. Opens it.\nINT./EXT. CABIN - NIGHT #81#\nBEN\nI will call a cab for you-\nWAIF\nThank you, thank you-\nBEN\nBut you wait here. Not inside.\nWAIF\nOK. OK sure. I mean, it\'s really cold- but... could you call now? Like right now?',
    '82': 'INT. LODGE ROOM - CABIN - NIGHT #82#\nBen shuts the door. Scans an old paper address book by the rotary phone, dials.\nRinging tone.\nSchrödinger jumps onto the windowsill again. Thru the glass, Ben can see the Waif rubbing her arms together. Shivering.\nINT./EXT. CABIN - NIGHT #83#\nBen opens the door.\nBEN\nThey\'re not picking up.\nWAIF\nShit... I mean, I guess it\'s late. They\'re busy.\nBEN\nDo you have someone I can call?\nWAIF\nNot out here. Look... I know it\'s not far to town to drive, you got a two-fifty right there-\nBEN\nNot gonna happen.\nShe looks around.\nWAIF\nIs there anywhere else close by?\nBEN\nNot walking. I don\'t even know how you got out here.\nWAIF\nOh shit, oh shit. I don\'t know what I\'m going to do.\nBEN\nMaybe tell me the story you cooked up right before I answered the door.\nWAIF\nI didn\'t cook up any story, OK? I had to run from some people. They\'re looking for me.\nBEN\nAnd what do you want me to do?\nWAIF\nTry for that cab again. Please.',
    '84': 'INT. JACK\'S ROOM - CABIN - NIGHT #84#\nA box of clothing is lying on the floor. Ben opens it. A woman\'s coat. He smells it for a moment. Checks the pockets, takes out an old credit card and lint.',
    '85': 'INT. LODGE ROOM - CABIN - NIGHT #85#\nBen reaches for the chain. Undoes it.\nINT./EXT. CABIN - NIGHT #86#\nBen opens the door wider and passes out the coat.\nBEN\nI\'m trying the cab again. Meantime-\nWAIF\nThank you, thank you.\nShe pulls on the coat. She backs up to the wall of the cabin, and hops on her feet to keep warm.',
    '87': 'INT. LODGE ROOM - CABIN - NIGHT #87#\nBen listens to the ringing tone on the receiver.\nIt rings out again.\nBEN\nFuck.\nINT./EXT. CABIN - NIGHT #88#\nBen opens the door. Waif steps into view.\nBEN\nThis might take a while.\nWAIF\nOK... OK. Can I ask... can anyone see this place from the road?\nBEN\nNo. Surprised you found it.\nWAIF\nThat\'s good, that\'s good.\nBEN\nWhat kind of people looking for you?\nWAIF\nThe worst kind.\nBEN\n(probing)\nWell if you want, I can call the police...\nWAIF\n_No_. No police*,* OK? No cops, I don\'t want cops involved. Promise me.\nBEN\nI\'ll try again.',
    '89': 'INT. LODGE ROOM - CABIN - NIGHT #89#\nBen closes the door. Rests his hand on it. Torn between a bad idea and his conscience.\nSchrödinger curls around his legs.\nINT./EXT. CABIN - NIGHT #90#\nBen opens the door again.\nBEN\nYou got anything on you I should know about?\nWAIF\nI got my phone, I got my notebook-\nBEN\nI mean a knife. I mean gear.\nWAIF\nYou think I am, a fucking junkie? I\'m not a fucking junkie, OK?\nBEN\nJust want to know what I\'m taking in here.\nWAIF\nI didn\'t mean to snap, either. You can search me if you want.\nShe steps back, and holds her arms away from body, inviting inspection. Rain catches on her skin.\nWAIF\nEven just a little while? I\'m freezing, I\'m wet, look I\'m... I\'m desperate, I\'m desperate OK?\n(...)\n*Please?*\nBen closes the door on her again.\nThere\'s a long moment as she waits.\nBen opens the door again, chain undone. He casts a glance left and right beyond her. Too dark to see anything.\nBEN\nHere\'s the deal. I\'ll let you in...\nWAIF\nOh thank you so much-\nBEN\nWAIF\nI don\'t care if it\'s messy-\nBEN\nI care.',
    '91': 'INT. LODGE ROOM - CABIN - NIGHT #91#\nBen sweeps the drug paraphernalia into a wastebasket. Moves to the kitchenette, feet crunching on glass.',
    '92': 'INT. KITCHENETTE - CABIN - NIGHT #92#\nBen trashes the rubbish in the trash can. Now he hunkers down and reaches for the dustpan and brush...\nThe lights go out.\nBen reaches up top and finds the electric lamp on the countertop. He finds the switch, and it strobes on. As he stands, he catches his reflection in the window. A basin of light surrounded by the dark of the forest.\nA SHRIEK OF PAIN from the lodge room.',
    '93': 'INT. LODGE ROOM - CABIN - NIGHT #93#\nBen pushes through the door, the lamp illuminating Waif. The door is open behind her, letting in the wind. She stands cradling Schrödinger in her arms. Her foot is cut and bleeding.\nWAIF\nI\'m sorry, I\'m sorry, I opened the door and the cat ran out, and my shoes were filthy, and-\nBEN\nJust don\'t move.\nBen closes and chains the front door.\nWAIF\nWhat about the lights?\nBEN\nThe storm. Or a wire. Hold still.\nHe gets down on his knees and begins to sweep up the glass around her anchoring foot into the dustpan. Blood drips from her cut foot to the floor.\nBEN\nPut your weight on me.\nShe leans on his shoulder as he guides her to the edge of the bed. He inspects the sole of the foot with his light. It glistens with blood and glass shards.\nBEN\nTry and keep your foot above your heart.\nBen goes to the open door. He opens it and looks outside.',
    '94': 'EXT. FRONT YARD - CABIN - NIGHT #94#\nBEN\'S POV: looking at the downpour outside. No signs of anyone in the clearing.',
    '95': 'INT. LODGE ROOM - CABIN - NIGHT #95#\nWaif watches his shirt hitch up, revealing the gun.\nHe turns inside to find Waif lying on the edge of the bed, her cut foot arched across her knee. She watches him.\nWAIF\nCan you lock the door? Can you lock the door please?\nBen bolts and locks the door.',
    '96': 'INT. BATHROOM - CABIN - NIGHT #96#\nBen opens the cabinet and grabs tweezers.\nHe notices a blue child\'s toothbrush. He slams the cabinet shut and lifts a pair of hand towels.',
    '97': 'INT. LODGE ROOM - CABIN - CONTINUOUS #97#\nBen places a basin of water down in front of Waif\'s foot. The floor is lined with blood-soaked hand towels.\nBEN\nGive me your foot.\nHe tweezes out the glass, dropping shards on the dustpan.\nBEN\nI\'m Ben. What do I call you?\nWAIF\nI was hoping you\'d remember.\nBEN\nWe don\'t know each other.\nWAIF\nOh, that\'s right. We don\'t.\nBen inspects her foot with the light of the lamp. Blood glints under its gaze.\nBEN\nI think I got all of it.\nTell me if you feel anything there.\nHe runs his fingers along the sole of her foot. He uses the ball of his palms to massage it gently. She shakes her head.\nHe picks up the bottle of moonshine from the desk. He splashes some on his hand and rubs it into her foot. Then he coils a bandage around her foot.\nWAIF\nI could use some of that.\nHe says nothing.\nWAIF\nWhere\'s the hospitality?\nBEN\nThis is the hospitality. I\'m calling you a cab.\nHe ties off the bandage and moves to the phone.\nWAIF\n(quiet)\nThat\'s new. Rotary phone?\nShe pulls out her notebook and scribbles something down. The pages are soaked, paper thin and ink running.\nBEN\nStill works.\nBen dials the cab number. Listens to the receiver.\nBEN\nYou got cash on you?\nWAIF\nEnough for a packet of cigarettes.\nBEN\nHow were you gonna pay the cab?\nWAIF\nSometimes when you come up short... they let you pay for it in kind.\nBEN\nI\'ll spot you.\nAfter a few more moments, he hangs up.\nBEN\nNo answer. Must be busy.\nWAIF\nAnd you really can\'t drive me...\nBEN\nI\'m not driving you.\nWAIF\nBecause you want me to stick around.\nBEN\nBecause I\'m wasted.\nWAIF\nNot just wasted, I\'d say.\nThe house lights come back on. The TV turns on, mute.\nBEN\nGuess they fixed the power.\nWaif finds the wall switch and turns off the house lights, leaving only the lamps.\nHe picks up the phone again and dials the cab number. It rings and rings.\nBEN\nThis might take a while.\nWAIF\nElectrics working... maybe you could fix me a coffee.',
    '98': 'INT. KITCHENETTE - CABIN - NIGHT #98#\nA Keurig coffee machine is gurgling. Ben wedges the door open so he can watch Waif. He sees her turning off the house lights, leaving only the lamps.\nWAIF\nIs it OK if I keep these off?\nBEN\nSure. I don\'t have any creamer, but it\'ll be hot.\nSchrödinger runs in and circles his feet. He pours water into a bowl and sets it down. It begins to lap it up.\nBen pours the coffee into a mug. The radio sounds from inside, playing music.',
    '99': 'INT. LODGE ROOM - CABIN - NIGHT #99#\nBen steps into the doorway to see Waif in the corner, her back to him. She has taken off her coat. The radio plays an *a cappella* trio harmonizing on the song “Washed in the Blood of Jesus.”\nHe sets the mug on the desk. Sits and watches her. She is moving to the song, her hips swaying to the old hymn.\nWAIF\nYou like this music? I like this music.\nBen watches her. The Waif closes her eyes, smiling to herself. Feeling it.\nWAIF\nMy dad was a preacher.\nBEN\nSurprising.\nShe sways, her dance a kind of sacrilegious movement to the music.\nShe stares at him. He holds her gaze.\nWAIF\nIs it OK if I... go wash up? I\'m thinking... I look like this the driver mightn\'t take me.\nBen says nothing.\nWAIF\nI won’t take long. Then we can do whatever you want, OK? Take me to town, let me stay here. Whatever you want.\nHe looks at her; dirty skin in dirty clothes.\nBEN\nYou can use the shower, but you need to keep that door open.\nWAIF\nSo you can watch me?\nBEN\nSo I can hear if you open the window.\nWAIF\nOh. I see. In case I got like a partner or something. Smart.\nShe moves towards the bathroom.\nWAIF\nYou can watch if you like.',
    '100': 'INT. BATHROOM - CABIN - NIGHT #100#\nWaif\'s wiry hands twist the shower on.\nThe water spits and chokes out of the faucet before the flow picks up. Waif’s shorts drop to the linoleum floor, her feet are filthy. Toes painted black, scratches on her legs. The shirt falls next.',
    '101': 'INT. LODGE ROOM - CABIN - NIGHT #101#\nSteam emerges from open door. Dirty shorts and vest are placed outside by her bare hands.\nBen moves to collect them. He can see her naked body but doesn\'t stare.',
    '102': 'INT. KITCHENETTE - CABIN - NIGHT #102#\nBen throws the vest into the washing machine. He can see the outside bathroom window from the kitchen.\nHe takes her shorts and inverts the pockets, patting them down, looking for contraband.\nNothing. No ID, dollars, nothing.\nHe opens her notebook. A dense ink scrawl. Wild and overlapping. Indecipherable notes.',
    '103': 'INT. LODGE ROOM - CABIN - NIGHT #103#\nBen opens the desk drawer. He untucks the revolver and pushes it deep within the drawer, closing it, noticing...\nThe picture of his family has been unwrapped. The paper stained a little from Waif\'s dirty fingers.',
    '104': 'INT. KITCHENETTE - CABIN - NIGHT #104#\nBen waits with the door to the lodge room ajar. A warped reflection of Waif dressing in new clothes.\nWAIF\nWhose clothes are these?\nBEN\nWhat do you care?\nWAIF\nHelps a girl to know why the man in the cabin has a set of women\'s clothes under his bed.\nBEN\nMy wife\'s. My ex-wife\'s.\nWAIF\nWe\'re the same size. Ain\'t that funny?\nAs she finishes dressing, Ben pushes through the door.',
    '105': 'INT. LODGE ROOM - CABIN - CONTINUOUS #105#\nBen moves past her to get to the phone. She doesn\'t move to give him space, forcing him to brush past her.\nHe notices a pool of water on the floor around her feet.\nHe walks to receiver and dials. Listening.\nWaif picks up a copy of THE RULES OF QUANTUM IMMORTALITY book and begins to leaf through.\nWAIF\nHey, this is a classic.\nBen looks at her from the phone.\nBEN\nYou know that book?\nWAIF\nAlmost wish I didn\'t. It\'s an info-hazard. Buries ideas so deep in your brainpan, you can\'t wedge them out with a screwdriver.\nHe hangs up again.\nBEN\nSo you know physics.\nWAIF\nIn a former life.\nDid you read this?\nBEN\nCouldn\'t make heads or tails.\nWAIF\nYou\'re on the right track.\nShe detunes the radio. Slides a dime from the table. She sits beside him. Shoulder to shoulder. She speaks softly.\nShe tosses the coin, and covers it with her hand.\nWAIF\nIt\'s heads or tails, right?\nBEN\nYeah. One or the other. Has to be.\nWAIF\nNot the way reality works when you go down all the way to the quantum level... it\'s heads and tails at the same time.\nThe murmur of crosstalk on the radio.\nWAIF\nLike that radio, I just tuned it between two stations. Now when I take away my hand...\nShe reveals it. It is tails.\nWAIF\nWaif stands and scoops up Schrödinger. She is comfortable in Waif\'s arms. Waif sets her in a cardboard packing box.\nWAIF\nLet\'s say you put kitty in a box. And along with kitty... you drop in a can of poison with a fifty-fifty chance of opening up.\nShe holds up Jack\'s little blue ball. Drops it in.\nWAIF\nIt\'s a coin toss whether kitty will live or die.\nWaif closes the lid over on Schrödinger, who remains strangely placid. The box is very still.\nWAIF\nFrom the outside, you can\'t tell if kitty is alive or dead. What the physics people say is, until you open the lid... kitty is alive and dead at the same time.\nShe takes Schrödinger out and tosses the toy under the bed. She runs under the bed to get it.\nBEN\nThat\'s not possible.\nWAIF\nA guy called Everett agreed with you.\nWaif sits beside Ben. She sets the book in his lap and shows a diagram of a branching universe. Her face is close to his.\nWAIF\nWhat Everett said was... *both* things happen. Every time there\'s two possible outcomes, the world splits into two more worlds. In one world, kitty lives. In another...\nBEN\nKitty dies.\nWAIF\nNow here\'s the fun part. This is why it\'s called quantum *immortality*. From Kitty\'s perspective, *it never dies*. Every time it\'s put in the box, it comes out alive. It\'s always bound to the surviving branch.\nBEN\nSo it lives forever.\nWAIF\nThat\'s what Everett believed.\nBEN\nAnd is he still alive?\nWAIF\nDied in nineteen eighty-two.\nBEN\nI\'d call that a refutation of his life\'s work.\nWAIF\nHe\'d just say he\'s dead in our timeline. Not his.\nThey stare at each other\nBEN\nI\'m going to try for that cab again.\nHe lifts the receiver. Taps the hook a few times. Silence.\nBEN\nNot good. No tone.\n(hanging up)\nMaybe the storm.\nWAIF\nOr...\nBEN\nOr what?\nWAIF\nOr it could be *them*.\nBEN\nYou\'re saying someone\'s cut the line?\nWAIF\nNo, I mean... I don\'t know, I\'m just saying... things like electricity and wires, they go a little haywire when *they\'re* involved.',
    '106': 'EXT. FRONT PORCH - CABIN - NIGHT #106#\nBen opens the door to lashing rain. Branches whipping back and forth. He holds the battery-powered lamp up to illuminate the roof.\nThe telephone cable remains taut. He tracks it into the treeline, where it disappears.',
    '107': 'INT. LODGE ROOM - CABIN - NIGHT  #107#\nBen comes in and locks the door.\nWith his back turned, Waif moves lightly toward Jack\'s room. She peers in. Ben moves to intercept her. Slams the door.\nBEN\nStay out of there.\nWAIF\nYou had a kid, right?\nBEN\nWhat\'d you say?\nWAIF\nI saw the kid\'s toothbrush in the bathroom. Your wife get custody?\nBEN\nEnough.\nWAIF\nOh shit. He died, right?\nYou keep that stuff in there like a mausoleum-\nBEN\nI said _that\'s enough_!\nWaif backs away from him. Frightened.\nWAIF\nI want to go now. I want to leave-\nBEN\nLook - I\'m sorry, I shouldn\'t have raised my voice.\nShe softens.\nWAIF\nNo, no, it\'s me, I didn\'t want to pry, it\'s just I\'m... I\'m in this place and I\'m wearing your wife\'s clothes and I\'m trying to get the picture, you know what I mean?\nBEN\nBut you\'re right. You should go. I\'ll drive you.\nBen moves across the room and grabs his keys.\nBEN\nYou got somewhere to stay in town?\nWAIF\nJust find me an open bar. I\'ll do fine.',
    '108': 'INT. KITCHENETTE - CABIN - NIGHT #108#\nBen extracts the rapid-washed clothing, bags them.',
    '109': 'INT. LODGE ROOM - CABIN - NIGHT #109#\nBen hands her the bag.\nBEN\nGot all your shit.\nWAIF\nThis is all my shit.\nThe lights flicker on and off... the dark revealing her face lit by flames from outside.\nThe lights return with a whine.\nBen moves to the window.\nBEN\'S POV: The fire pit is lit.\nSchrödinger begins to screech and moves to the door, pawing to get out, turning around and around.\nBen looks closer at the flames; they have a pale blue shade, a strange pattern like St. Elmo\'s Fire.\nBen takes lamp and moves to the door. He unbolts it.\nBEN\nI need to go outside-\nWAIF\nDon\'t do that, please don\'t-\nHe undoes another lock.\nBEN\nJust stay in here until I call you.\nBen undoes the final lock. He reaches for the drawer handle... hesitates. Waif *clocks* the reach.\nBen reaches instead for a fire poker.\nHe opens the door and Schrödinger squeezes out the gap.',
    '110': 'EXT. FRONT YARD - CABIN - NIGHT #110#\nThe exterior is lit by the pale flames. Ben steps into the illumination.\nHe casts around his lamp. It reflects off the damp bark. The trees sway with the wind. Rain lashing on the surfaces.\nBen moves toward the fire pit. Despite the wind and the rain the fire is able to burn.\nHe looks at the strange flames. Almost electrical. He reaches out a hand. No heat.\nHe walks to the pickup. Casts the lamp inside to make sure it\'s empty. He unlocks it and gets inside.',
    '111': 'INT. PICKUP - NIGHT #111#\nBen locks the door. Turns the keys. The dash lights up. The engine won\'t start. He tries again. No engine.',
    '112': 'EXT. PICKUP - NIGHT #112#\nBen opens the hood. He is lashed with rain and wind. He casts the lamp inside to inspect it. His hands trace some of the mechanisms of the engine.\nTwo cables are black and burnt out.',
    '113': 'EXT. FRONT YARD - CABIN - NIGHT #113#\nBen walks back toward the cabin. The fire is out. He draws closer and looks at the fire pit.\nThere is no smoke. Nothing is even charred.\nHe mounts the porch and tries the door. _Locked_.\nHe bangs the door.\nBEN\nHey! Open up!\nBen looks into the gloom. Shadows moving in the trees.\nBEN\nOpen the goddamned door-\nIt is opened by Waif... chain on, breaking her visage. He wedges his foot in the door so she can\'t close it again.\nBEN\nWhy\'d you lock the fucking door?\nWAIF\nI got frightened.\nBEN\nOpen up.\nWAIF\nTake your foot out of the door, so I can take off the chain.\nSlowly, he slides it out. She closes the door and opens up.',
    '114': 'INT. LODGE ROOM - CABIN - NIGHT #114#\nBen brushes past her and slams the door shut and locks it.\nBEN\nThe solenoids are shorted. It\'s not going anywhere tonight.\nHe notices the desk drawer is open... Waif is holding the revolver at her side.\nWAIF\nYou didn\'t tell me you had a gun.\nBEN\nGive it to me.\nHe moves toward her but she holds it loose and unpredictably, causing him to halt.\nWAIF\nMaybe I hold onto it. You\'re a strange man. You scared me earlier.\nBEN\nYou came into my cabin.\nYou\'re the stranger here.\nWAIF\nMatter of how you see it.\nNow she eases toward him, gun loose in her hands.\nWAIF\nWhy do you have a gun?\nBEN\nIt\'s not mine.\nWAIF\nOh yeah sure. Gun that isn\'t yours is just lying around the cabin. Don\'t lie to me. You brought it here, didn\'t you?\nBEN\nPlease. Just give it to me.\nWAIF\nThe pain isn\'t getting any easier, is it Ben?\nBEN\nPlease-\nWAIF\nWhat were you doing when I knocked on your door? What were you doing when that strange dirty girl knocked on the door to daddy’s cabin?\nShe passes the gun from one hand to another. Now she moves into space in the other side of the room.\nWAIF\nYou want to see a neat trick, Ben?\nShe sticks the gun to her head.\nBEN\nNOOOO!\nShe pulls the trigger... CLICK.\nBen races across the room toward her.\nShe squeezes the trigger - CLICK...\nand squeezes again - CLICK...\nBen pushes the gun away from her head... it FIRES.\nThe round hits the wall. Splinters explode.\nHe gets the gun in his hand, pushing her away. She is energized and laughing.\nWAIF\nGuess I\'m lucky that way.\nBen tucks the gun back in his waist. Breathes out the shock, works out what to do.\nBEN\nTime for you to go.\nWAIF\nYour car isn\'t working-\nBEN\nI don\'t give a rat\'s ass about the car. You\'re fucking out of here, OK?\nWAIF\nYou gonna put me out in the cold and the rain?\nBEN\nYou go outside in what you have, or with that. Your choice.\nShe moves to the door. Unchains it...\nWAIF\nThey\'re out there, I told you-\nBEN\nI don\'t know who the fuck *they* are, and I don\'t care, OK, I don\'t care what crazy shit you have swimming around your head, you go. You go now.\nShe doesn\'t move.\nWAIF\nWhat are you gonna do? Hit me? Stick the gun in my face?\nBEN\nFine. Play it your way.\nHe goes past her to the phone and lifts it. A dial tone.\nBEN\nGot a line. You don\'t go, I dial three digits.\nShe stares him down... then stands.\nPicks up the coat. Moves toward the door as she pulls it on. He sets down the receiver.\nShe opens the door. The sound of the rain comes in.\nShe backs inside. She closes the door.\nWAIF\nI saw your arm.\nHe says nothing.\nWAIF\nBlack ink is easier to get off. Colors, not so much. I think I saw... I think I saw an ace of hearts. Which crew is that? New Mexico? Long way from home Benny.\nHe says nothing.\nWAIF\nYour old pals know you\'re out here?\nBEN\nI\'m warning you. You need to go-\nWAIF\nYou patch out? Or turn States?\nHe lifts the phone and dials nine... one...\nBEN\nI\'m calling the police-\nWAIF\nNo you\'re not *Ben*. That even your real name? Because you didn\'t turn States, did you? You like the police little as I do. And no way you got a license for that gun.\nShe sits down on the bed and puts her arms behind her and arches her back. He grips the phone.\nWAIF\nOne more digit to go. Shove, or fold.\nSlowly... he sets the phone down. Moves in front of her.\nBEN\nGet the fuck up.\nHe tries to yank to her feet by the arm but she resists.\nWAIF\nI can be discreet. I can be indiscreet. You put me out there... which do you think I\'ll be? How long do you think word gets back out west to your old friends?\nHe says nothing.\nWAIF\nBars on the windows. Triple locks on the door. Case any of your old crew discovers who you are now, right?\nHe pulls her up to her feet. Gets right into her face.\nBEN\nIf I really am the person you say I am... and what you say about me is true... then you\'re out here on your own. With me?\nWAIF\nOh, you gonna hurt me Benny? You gonna fuck me? Is that a pinky promise?\nHe lets go of her and backs away from her. He turns his back. He feels completely vulnerable. He looks at the bottle of moonshine.\nWAIF\nYou used to hurt people, right? That what you did for your crew?\nHe uncorks it and drinks from the bottle.\nWAIF\nMight as well. Saying as you\'re not driving anywhere.\nHe rests against the desk and looks at her.\nBEN\nI was a wrench. I fixed problems.\nWAIF\nAll sorts of problems, right?\nShe stands and moves toward him. She drops her hand by his waist and snatches the bottle from him. Takes a swig.\nWAIF\nGo get some glasses.\nWaif locks and bolts the door.\nCUT TO:\nBen sets two glasses on the desk. Fills two smudged glasses with moonshine. Gives her one.\nHe sits on the edge of the bed. Drinks heavily.\nBEN\nIt was in our car.\nWAIF\nLike a crash?\nBEN\nNo. Not quite.\nI was driving and...\n(closes eyes)\nIt was a jumper. Came in through the roof.\nWAIF\nShit. Nothing you could do, right?\nBEN\nIf I had just left the house a few seconds earlier, a few seconds later... if I had stepped on the gas a little, or eased up on it, or tapped the brakes a little more... he\'d be seven now.\nWAIF\nThere\'s a lot of worlds out there where it didn\'t happen.\nBEN\nNot the world I\'m in.\nWAIF\nNot the world you need to stay in.\nShe needles her way between his legs.\nWAIF\nI know what it feels like. You just want it to stop, right?\nBEN\nI want it all to stop.\nWAIF\nAnd what would you be willing to risk to see your son again?\nBEN\nThat\'s not possible.\nWAIF\nIndulge me. What if it was...?\nHe finishes his glass. Looks at her, eyes watering.\nBEN\nEverything. I\'d risk everything. And I\'d do anything.\nShe sits beside him. Hip to hip. She runs her hand up and down his back. She eases the revolver from his waistline... he lets her... and she dangles the gun over his shoulder.\nWAIF\nThere\'s a way to be with your son.\nWaif runs the muzzle up the side of his head. Strokes his temple.\nBEN\nThere\'s no after-life.\nWAIF\nMaybe. But there\'s many worlds out there. Worlds where your son is still alive.\nBEN\nNot the world I\'m in.\nWAIF\nNot the world you\'re in... right now.\nShe lowers the gun and rests her head on his shoulder.\nWAIF\nThere\'s a way of getting there.\nHe looks at her. Unsure what this is.\nBEN\nLet\'s play your little game. How do I get there?\nWAIF\nYou point this gun at your head and you pull the trigger.\nA moment of silence. He laughs.\nBEN\nNice try. Pretty cold way to rob someone. Get them to shoot themselves so you can clean the place out. You don\'t even have to worry about a murder rap.\nShe gets up and kneels in front of him.\nWAIF\nI want you to imagine you and I are sitting here. At the split in a river. One future flows one way, another future flows the other.\nShe sticks the gun to her head.\nBEN\nGonna try your parlor trick again?\nShe pulls the trigger before he can react...\nBEN\n_Fuck-_\nHer image doubles into split-screen.\nIn one panel, the gun clicks - no shot, alive.\nIn the other, she blows her brains out. The gore and chaos in that frame runs parallel to her calm speech in the other.\nWAIF\nIn one world I\'m lying on the ground right now, and you\'re slipping on my brains trying to revive me. And in the other, I\'m looking right at you. Finger on the trigger for the next round. Every time I pull the trigger, the river splits, and splits again.\nThe living Waif points the gun at her head. Pulls the trigger, creating two more screens. We now watch a quadraphonic image - three dead, one living and talking.\nWe move into the living frame until it fills the screen.\nShe plants the gun against the floorboards and _fires_ loudly.\nBen reacts. Wood splinters.\nThen back to her head, clicks again. Nothing.\nWAIF\nThink this is a parlor trick?\nBEN\nHow did you do that?\nWAIF\nI\'m the cat in the box.\nShe opens the revolver and shows him the four remaining rounds inside. Snaps it shut.\nSticks it to her head and pulls the trigger. _Nothing._\nBEN\nDoesn\'t make sense-\nWAIF\nWhat is real is real, even if we can\'t comprehend it.\nShe places the revolver in his hands, gently.\nWAIF\nThe branch of reality I survive in... is not the branch I came from. Things are always slightly different. It\'s like you\'re standing still but moving between worlds.\nShe guides the gun to side of his head.\nWAIF\nDo it.\nBEN\nI don\'t... I can\'t...\nWAIF\nYou said you\'d risk everything?\nIf it works... you\'re with them. It doesn\'t... your pain is over. What\'s to lose?\nHe drops the gun away from his head. Looks to the table-top with the photo of his family.\nBEN\nYou\'re lying to me. I know you\'re lying to me.\nWAIF\nYou said you\'d do anything. I\'ve shown you miracles. Can you show me courage?\nHe sticks the gun to his head and pulls the trigger.\nIt CLICKS - no shot.\nHe gasps as -\nThe lights flicker off.\nThe room is lit by pale fire from outside. Waif is suddenly frantic, moving deeper inside the cabin.\nWAIF\nThey\'re here. They\'re here.\nBen moves to the window. He looks out to see:',
    '115': 'EXT. FRONT YARD - CABIN - NIGHT #115#\nA figure steps in front of the fire. This is THE MOTHER. 50s, wild-haired, head low in a long man\'s coat.\nShe stands eerily still.',
    '116': 'INT. LODGE ROOM - CABIN - NIGHT #116#\nBen breaks from the window. Fear sobering him up.\nBEN\nWho the fuck is she?\nWAIF\nDon\'t open the door. Please don\'t open the door-\nBEN\n_Is she armed_?\nWAIF\nHe begins to open the locks.',
    '117': 'EXT. FRONT YARD - CABIN - NIGHT #117#\nBen emerges, fire poker at his side. The Mother begins to move toward him.\nBEN\nBack away, whatever the fuck you are.\nThe Mother\'s eyes lock on Ben. Greasy hair covering her face. She trudges forward in brown boots. He raises the fire poker.\nBEN\nBack away.\nThe Mother steps through the fire. Its strange flames do not burn her.\nBEN\nOh fuck...\nShe starts fast toward Ben. He backs away inside-',
    '118': 'INT. LODGE ROOM - CABIN - NIGHT #118#\nBen reaches for the lock, turns it as-\nThe Mother SLAMS into the door.\nThe door glass is smashed... a hand reaching in for the lock. Turning it...\nBen slices with the fire poker. He looks to Waif who is cowering in the corner.\nBEN\nUse it! USE IT!\nShe is catatonic...\nThe hand reaching for the handle...\nThe fire poker SMASHES against it.\nBen swings again and again until the hand withdraws.\nThe Mother\'s shadow is projected across the room through the flames... she begins to saunter around the house.\nBEN\nWatch the door.\nWaif says nothing. Both hands clutching the gun closely.\nBen tracks the path of the Mother. He can see the shadow of the Mother passing the window. Backlit by the pale flames of the fire pit.\nBen sees the next window is open.\nHe rushes to shut it... a hand *stretches* and coils through the bars. He SMASHES the poker against the hand.\nThe hand coils and grabs Ben\'s wrist. Yanks his arm out the window until face slams tight against the bars.\nMother\'s face presses against the bars... her mouth opening *unnaturally* wide and moving closer to his face.\nWaif screams and sticks the gun to her head.\nShe pulls the trigger and-\nBen collapses onto the ground.\nThe Mother is gone. Into air.\nHe scouts the window, looking for her. Nothing.\nHe pulls the window down, bolts it down tight.\nHe looks at Waif, the gun still pressed to her head. He moves over to her and gently takes it away from her.\nBEN\nThought you pulled the trigger.\nWAIF\nI did.\n(off his look)\nTold you I was lucky, right?\nBen moves to the door. Unbolts it.',
    '119': 'EXT. FRONT YARD - CABIN - NIGHT #119#\nBen swings the gun left and right. Clearing corners.\nThe fire pit is extinguished. Smokeless and still. Not even burning ash. He moves toward it... dipping a hand.\nIt\'s cool to his touch.',
    '120': 'INT. LODGE ROOM - CABIN - NIGHT #120#\nBen returns and closes the door, bolting it.\nWAIF\nI think we\'re OK.\nThey only come at night.\nHe triple locks the doors. She checks the windows, makes sure the bars are locked.\nBEN\nWho the fuck were *they*?\nWAIF\nThis isn\'t in the book... this is something I discovered... but you move between worlds... you sorta weaken the walls between them... and *they* squeeze in through the cracks.\nBEN\nWhat are they? Like people?\nWAIF\nNot us. Not human. At least not anymore. They embody themselves in people who shouldn\'t be here. Try to bend reality back to the way it was. Reversion to the mean.\nHe evaluates what to do. Tiredness seeping over him.\nHe moves to the doorway to Jack\'s room. Opens it.\nBEN\nI can\'t fix the car in the rain. When it clears I\'ll drive you into town.',
    '121': 'INT. LODGE ROOM - CABIN - NIGHT #121#\nBen lies awake in bed. Trees creak in the wind outside. A branch tapping out morse against a window.\nThe box room door opens and Waif emerges. She crawls into the bed beside him.\nWAIF\nI\'m cold.\nHe doesn\'t move. His breath becomes heavy. He looks at her bare back. An elaborate caduceus tattoo on her back, two snakes coiling around each other.\nShe pulls the blanket to cover herself.',
    '122': 'INT. LODGE ROOM - CABIN - DAY #122#\nBen wakes with the hangover of the century.\nHe looks around to find he is alone. Front door shut. Somewhere, he hears a dry-clicking sound...\nCUT TO:\nBen with the fire poker, moving toward the kitchenette and the growing sound of clicks. Pushing on the door to find...',
    '123': 'INT. KITCHENETTE - CABIN - DAY #123#\nThe revolver sits on the worktop. Beside it, the Geiger counter - clicking.\nBen sets down the poker. Picks the Geiger counter up. As it is moved away from the gun, the clicking slows to background. He runs it across the revolver. It flares up.\nHe runs the counter over it... the zone of interest is the cylinder. He takes out the shells.  Runs the counter over them. Background level.\nHe runs it over the empty cylinder. Counter clicks faster. He rotates to the sealed chamber. The clicking _saturates_.',
    '124': 'EXT. PICKUP - DAY #124#\nBen uses a solder to replace the burnt solenoids.',
    '125': 'INT. PICKUP (MOVING) - BROKEN BOW - DAY #125#\nBen turns on the radio habitually. The radio comes through, loud and clear. He twists the volume knob back and forth, testing it. It *works,* which is strange.',
    '126': 'EXT. MAIN STREET - DAY #126#\nBen walking up the main street. Everything more genteel and less run down.\nHe reaches SECOND LAST CHANCE SUPPLY.',
    '127': 'INT. SECOND LAST CHANCE SUPPLY - DAY #127#\nBen enters the store. Everything is slightly newer, less scrappy. Jamie appears from the backroom.\nBEN\nYou sold this ammo. I had a misfire.\nJAMIE\nThose were brand new. Maybe the gun.\nBEN\nYou want to check it?\nCUT TO:',
    '128': 'INT. BACKROOM - SECOND LAST CHANCE SUPPLY - DAY #128#\nJamie works at a bench inspecting the revolver. Testing the hammer mechanism, checking the barrels for imperfections.\nJAMIE\nAutomatics, they can jam, they can fuck up anytime. But the thing about revolvers is, they\'re simple pieces of equipment. They\'re a hammer and a hunk of metal.\nBEN\nShe\'s working fine.\nJAMIE\nFar as I can tell.\nBEN\nThey ever use radioactive material in old guns?\nJAMIE\nRadium sights. Tritium in some night-scopes. Nothing in a gun like this, far as I know.\nJamie places the piece back in the box.\nJAMIE\nInteresting little item. I mean, I can buy this off of you, if you looking to sell.\nBEN\nThought you didn\'t buy used guns.\nJAMIE\n(confused)\nHalf my custom.',
    '129': 'INT. SECOND LAST CHANCE SUPPLY - DAY #129#\nJamie and Ben emerge from the backroom. Ben picks up a pack of Tylenol and drops down a bill on the counter.\nAs Jamie tills it, Ben hears a mechanical whir. He looks to see Ricky in a motorized chair moving down the aisle.\nBEN\nYour son get new wheels?\nJAMIE\nNot yet... but battery is running low. Basically means a new chair.\nBen takes the Tylenol and the case. He sees the store name on the receipt jotter. It reads\n!SECOND LAST CHANCE SUPPLY.',
    '130': 'EXT. SECOND LAST CHANCE SUPPLY - DAY #130#\nBen stares at the sign. It appears used and worn, not new.',
    '131': 'EXT. CLEARING - NEAR CABIN - DAY #131#\nBen holds the gun. Aims it at a tree ten feet away.\nFires. Bark explodes in a series of six shots.\nHe reloads the chamber.\nFires again, six shots ripping into the bark. He inspects the gun. Opens and closes the cylinder.',
    '132': 'EXT. FRONT YARD - CABIN - DAY #132#\nBen returns with gun in hand. He finds Waif sitting on the swing. Hunched, smoking, easing the seat back and forth.\nWAIF\nSo what I figure is... it\'s got some sort of radioactive element inside the chamber. Like radium. Losing a neutron and a proton every few seconds. And there\'s some sort of a particle detector in the housing and the trigger only works if the detector picks up a decaying neutron in that moment, or vice versa, it doesn\'t really matter which.\nBEN\nWhat are the chances of that?\nWAIF\nIt should happen every time. But it depends on your interpretation of the Heisenberg principle.\nWaif reaches and picks up the book on Quantum Immortality.\nWAIF\nThe cat is alive or dead until you open the box. But when you open the box, it\'s always been dead. What Everett argued is... from the perspective of the cat, it never dies. It\'s always alive.\nBEN\nSo if I stick this to my head and pull the trigger...\nWAIF\nEverett would say from your perspective, the decayed neutron isn\'t detected... trigger doesn\'t work. You go on living. Whereas everyone else is left with the mess.\nHe raises the handle of the gun toward her.\nBEN\nThis is yours?\nWAIF\nNo.\nBEN\nYou plant it in my house?\nWAIF\nIt wasn\'t me.\nBEN\nThen who did?\nWAIF\nYou\'ll work that one out on your own, Benny. You notice anything different?\nBEN\nYeah.\nWAIF\nLike what.\nBEN\nThe name of the store was different. Kid had a new wheelchair.\nWAIF\nMandela effects. Reality changes but we have the old memories.\nBEN\nThis is bullshit-\nWAIF\nWhat does the evil queen say in Snow White?\nBEN\nMirror mirror on the wall-\nWAIF\nNo. She says *magic mirror *on the wall. People remember things that never happened. Nelson Mandela dying in prison. Sinbad was in a genie movie called Shazaam. Fruit of the Loom had a cornucopia in its logo. You know what those are? Ripples in reality. Shadows of a world that no longer gives any light.\nShe moves to the book and opens it and presents it to him.\nWAIF\nOur memories remain intact, even if the world doesn\'t. Even if reality fractures, and morphs, and coils itself another way.\nWAIF\nWanna hear a story?\nBEN\nI\'m not in the mood.\nWAIF\nMy story. Mine.\nShe pats the spare seat beside her.\nWarily, he sits down.\nWAIF\nI\'m gonna tell you about the first time I died... or rather, the first time *I didn\'t*.\nShe drags on the cigarette and offers it to him. He takes it and smokes.\nWAIF\nIt begins with *the man*. He drifted into her life and extended his hand.',
    '133': 'INT. INSTITUTION - DAY #133#\nA long corridor. Murmurs of the unwell.\nWaif is huddled against a wall. Tears streaming down her face... which falls under shadow. She looks up to see...\nTHE MAN. A dark, backlit figure. He offers his hand.\nShe finds some sort of confidence. Reaches out-',
    '134': 'EXT. FRONT YARD - CABIN - DAY #134#\nWAIF\nHe became her oak tree. His arms became the branches that held her aloft.',
    '135': 'INT. BEDROOM - DAY #135#\nThe Man and Waif coil in the bed together. He lifts her up with his arms, her whole weight resting upon him.',
    '136': 'EXT. DRIVEWAY - DAY #136#\nDreamlike, we follow behind the Man and Waif as they walk. Her arm slides under his arm and she nestles him.\nWAIF\nShe felt the roots of their being growing intertwined. Until...',
    '137': 'EXT. FRONT YARD - CABIN - DAY #137#\nWaif takes a drag on the cigarette again.\nWAIF\n(through pain)\nUntil he... until he left her.',
    '138': 'INT. HALLWAY - DAY #138#\nWaif arrives inside. She notices the emptiness immediately. A missing presence.\nWAIF\nNo note. No message.',
    '139': 'INT. BEDROOM - ON WAIF - DAY #139#\nOpening a closet. Half empty.\nOn Waif: rising worry.\nWAIF (O.S.)\nJust the space he left in her life. By negation. Burned inside her.',
    '140': 'EXT. NEW YORK STREET - DAY #140#\nTopdown from skyscraper to ground level. Waif stakes out the front of an office building.\nThe Man emerges from a revolving door. She bolts after him.\nWAIF (O.S.)\nShe tries to speak to him. Tries to see him. And yet...\nThe Man ignores her as a DOOR GUARD blocks her path and grabs her. She SCREAMS after him, her face a mask of desperation and pain.',
    '141': 'EXT. FRONT YARD - CABIN - DAY #141#\nWAIF',
    '142': 'INT. OFFICE RECEPTION - DAY #142#\nWAIF in a long coat enters and brushes past a secretary. She pushes her way through a glass door.',
    '143': 'INT. CORNER OFFICE - NEW YORK - DAY #143#\nWaif moves toward The Man. He stands in front of a glass wall looking out onto New York City.\nShe produces a *revolver.*\nWAIF (O.S.)\nShe was going to show what it felt like not to exist.\nShe FIRES, shattering the glass. She aims it at her head.',
    '144': 'EXT. FRONT YARD - CABIN - DAY #144#\nWAIF\nSo she kills herself in front of him. At least, that was the plan.',
    '145': 'INT. INTENSIVE CARE BED - HOSPITAL - DAY #145#\nWaif lies in bed. Her head is swabbed in a bandage.\nWAIF (O.S.)\nShe wakes in hospital. A miraculous survival. That\'s when she first sees them.\nA figure in the doorway. Dressed in a nurse\'s uniform, but resembling The Mother. Staring at Waif.\nWAIF (O.S.)\nThe others.',
    '146': 'INT. INTENSIVE CARE BED - NIGHT #146#\nWaif wakes up. She feels something under her pillow, taking out... a pair of pliers.',
    '147': 'INT. STORAGE ROOM - HOSPITAL - NIGHT #147#\nWaif is in hospital overalls. She uses the pliers to shear the rubber housing off a cable, exposing the bare copper.\nShe plugs the cable in. Sparks the copper wires together.\nShe stabs the cables against her arm... blowing her across the room against the metal storage frame.',
    '148': 'EXT. FRONT YARD - CABIN - DAY #148#\nBen studies her carefully.\nWAIF (O.S.)\nRubber shoes. Not so smart. Then, they send her to the loony bin. For her own protection. Watched so she cannot take her life again.',
    '149': 'INT. CORRIDOR - INSTITUTION - DAY #149#\nThe Woman is in patient overalls sweeping the floor. A GUARD stares at her.',
    '150': 'INT. PATIENT ROOM - NIGHT #150#\nWaif is on her knees giving the Guard a blowjob. Her hand slips up his side and carefully unclips his keyring.\nWAIF\nBut she finds a way.',
    '151': 'EXT. INSTITUTION - NIGHT #151#\nAlarms sounds as Waif scales a perimeter fence...\nCalmly steps in front of it.',
    '152': 'INT. DRIVER COCKPIT - NIGHT #152#\nThe driver sees WAIF directly in his path. He slams on the brakes but...',
    '153': 'EXT. FREEWAY - NIGHT #153#\nWaif is ripped underneath the 18-wheeler...',
    '154': 'EXT. FRONT YARD - CABIN - NIGHT #154#\nWAIF\nYet she finds herself surviving in another-\nPARAMEDIC (PRE-LAP)\n*Miracle.*',
    '155': 'EXT. FREEWAY - NIGHT #155#\nWaif is being hoisted on a stretcher. Bleeding and bruised but alive.\nPARAMEDIC (CONT\'D) (O.S.)\nShe is placed in an ambulance.',
    '156': 'INT. PADDED ROOM - DAY #156#\nWaif is in a straitjacket. Some time later.\nWAIF (O.S.)\nSo they lock her away. They bind her so she cannot try again.',
    '157': 'EXT. FRONT YARD - CABIN - DAY #157#\nWAIF\nAfter a while you\'re not sure which memories are real. Which memories are another self. Another you. It feels sometimes like... like you\'re *legion*. The tributary of a river of souls.\nWaif looks to Ben.\nWAIF\nAfter a while I learned how to control it...\nBEN\nHow?\nWAIF\nI was shown how.',
    '158': 'INT. PADDED ROOM - DAY #158#\nA PSYCHIATRIST whose face we cannot see is led into the room. A chair is placed in the corner.\nPsychiatrist places a book in front of Waif. Waif opens the pages. It is THE RULES OF QUANTUM IMMORTALITY.\nWAIF (O.S.)\nI was given the book.',
    '159': 'EXT. FRONT YARD - CABIN - DAY #159#\nBen looks at her. He lifts the book and opens it at the title page.\nBEN\nYou ever talk to the guy who wrote the book.\nWAIF\nNot possible.\nBEN\nWhy not?\nWAIF\nBecause he killed himself.\nAt least from our perspective.\nShe turns the page to the publication address.\nWAIF\nBut maybe you want to check it out for yourself.',
    '160': 'INT. PICKUP (MOVING) - FOREST ROAD - DAY #160#\nBen has the Quantum Suicide book open on his lap. He dials the address into the old GPS deck on his truck...\n1475 SHORE ROAD...\nIt calculates the journey.\nFADE TO:',
    '161': 'EXT. UPSTATE NEW YORK - DAY #161#\nBen\'s pick-up drives along the freeway. Strangely empty. Everything gray.',
    '162': 'EXT. NORTH HAVEN - DAY #162#\nBen\'s pickup enters an old-money enclave.',
    '163': 'INT. PICKUP (MOVING) - NORTH HAVEN - DAY #163#\nBen looks out at the old mansions. They stand back from the road behind big gates and railings. He stops.\nFramed through the windshield is a wrought-iron gate framed by stone pillars. Beyond it, a Colonial Revival mansion.\nBen gets out and walks into frame. The gate is chained but loose. Ben squeezes through.',
    '164': 'EXT. MANSION - DAY #164#\nBen walks towards the mansion. No lights or signs of life.\nA gravestone in the garden among the leaves. Ben walks toward it and inspects it. He wipes grime and leaves off the gravestone.\nIt reads.\nQ.R. PRESTON\n1946-2014\nNEXT TO HIS BELOVED, JOYCE\nWOMAN (O.S.)\nI should keep it better.\nBen startles and turns. An OLD WOMAN stands there. Dressed for a Sunday dinner.\nWOMAN\nAre you delivering?\nHe stands.\nBEN\nI was looking for Mr Preston.\nWOMAN\nLooks like you found him.\nBEN\nAre you a relative?\nWOMAN\nI\'m his widow. Joyce.\nHe looks to the name on the gravestone.\nJOYCE\nI\'m not the undead. I was getting it chiselled for Preston, figured I might as well for me. Save the state some dollars.',
    '165': 'INT. KITCHENETTE - MANSION - DAY #165#\nJoyce gingerly opens the copy of THE RULES OF QUANTUM IMMORTALITY. She leafs through the pages. Ben sits across from her, reading her reaction.\nJOYCE\nBeen a while since I seen this.\nBEN\nHe believed it, right?\nJOYCE\nOh yes. And not just his writing. He\'d make experiments. Build things. Do you want to see his lab?',
    '166': 'INT. PRESTON\'S LAB - DAY #166#\nStriplights flicker on. Ben and Joyce stand at the threshold, and we move across the room... vintage radiology equipment. Capacitor banks.\nJOYCE\nI should try and get it appraised... I\'m just worried there\'s some isotopes lying around he shouldn\'t have.\nBen moves through the lab slowly inspecting things.\nBEN\nRadioactive.\nJOYCE\nMight lead to questions to where he got it. Some of his old Los Alamos buddies. I don\'t think I\'m liable, but nonetheless I don\'t want a bunch of men in hazmats running around my house.\nBen finds a part of the laboratory with lathes. Engineering equipment. He notices some metal gun barrels under plastic.\nBEN\nHe was an armorer.\nJOYCE\nYes. He\'d make his own guns. It was one of his own guns that killed him.\nHe looks at her.\nBEN\nCan I ask-\nJOYCE\nDid he shoot himself?\n(...)\nNo... no.\nShe runs the memories through her head.\nJOYCE\nWe had a break-in. Two junkies looking for cash and valuables. They were masked up. Vicious animals. They coshed me here on the crown.\nShe runs a finger on her forehead where there\'s a scar.\nJOYCE\nQuerry tried to... he tried to do something. Then they got to work on him. Looking for a safe, valuables. So he... he pulls his gun. I wasn\'t seeing clearly because of the blood in my eyes... but... it looked like he pointed the gun at himself. And he pulled the trigger. Once, twice, three times... and I remember thinking why did he pick something that wouldn\'t work. And then one of them, took the gun off him. Pointed it at him. And fired. Then it worked.\nShe moves to the lights.\nJOYCE\nIf it\'s OK with you, I think that\'s enough of memory lane.\nBen nods and stands.',
    '167': 'EXT. BRIDGE - DAY #167#\nThe pickup crosses the bridge. We drift beyond it to look at the black water beneath.',
    '168': 'EXT. SUBURB - DAY #168#\nBen\'s pickup turns onto his street.',
    '169': 'INT. PICKUP (MOVING) - SUBURB - DAY #169#\nBen breathes heavily. Eyes on every passing house. Every neighbor he knows. Getting close to:\nBEN\'S POV: His house, coming up alongside him.',
    '170': 'EXT. SUBURB - DAY #170#\nBen pulls up the other side of the street. Observing. It looks different to before. The garden is tended. There is no for sale sign.\nThe shutters are pulled on the outside.',
    '171': 'INT. PICKUP - DAY #171#\nBen watches the house. Looking for movement. There is none.',
    '172': 'EXT. DRIVEWAY - HOUSE - DAY #172#\nBen approaches the house.\nA fresh flowerbed - small handprints in the clay. He traces them with his fingers.\nHe walks to the door. Takes his key out. His hand is shaking. He slots it in and turns it-\nDoesn\'t work. He tries it again, and again.\nNEIGHBOR (O.S.)\nHelp you?\nBen looks across to the picket fence. An ELDERLY MAN stands there, watching him. He\'s holding a hedge trimmer.\nBEN\nYou working Terry\'s house?\nNEIGHBOR\nI don\'t know a Terry.\nBEN\nI\'m just looking for Marie.\nNEIGHBOR\nAnd I\'m not giving names. The people who live there, I kinda look out for them. So maybe come back when they\'re expecting you.\nBen slips his key in his back pocket.\nBEN\nI\'ll try another time.\nNEIGHBOR\nMaybe better if you leave a number. I\'ll make sure she gets it.',
    '173': 'INT. PICKUP - DAY #173#\nBen stakes out the house from down the street. A Prius passes him... dark windows... and turns into his house.\nThe garage door opens and they enter.',
    '174': 'EXT. HOUSE - DAY #174#\nBen walks toward the house. He sees NEIGHBOR tweaking a curtain. Catching him.\nBen moves up the driveway quickly. He knocks the door. Again, no answer.\nBen bangs on the door.\nBEN\nMarie!\nThe Neighbor has emerged.\nNEIGHBOR\nFella, I\'ve called the police.\nBen walks round the house. Tries to see through the windows.\nBEN\nMarie! Jack!\nHe can see DRAWINGS on the ground through the slats.\nBEN\nListen, I\'m her husband.\nNEIGHBOR\nThat\'s... that\'s *not* possible.\nHe reaches the kitchen window and sees Marie, who is on the phone. He looks at her. He smiles. A miracle.\nShe freezes at the sight of him.',
    '175': 'INT. KITCHEN - HOUSE - DAY #175#\nMarie looks at him through the screen door.\n_SCREAMS_.',
    '176': 'EXT. BACKYARD - HOUSE - DAY #176#\nBen tries to open the screen door.\nBEN\nHey, hey, hey - it\'s me. Calm down. It\'s me!\nHe rattles the door- Marie screams again.\nNEIGHBOR\nThis has gone on far enough -\nThe Neighbor unlocks the dividing fence to get to Ben.\nBen tries his key in the screen door - not working. He punches through the netting and unlocks the door.\nMarie collapses backwards at he pushes it.\nBEN\nIt\'s me, it\'s Ben, I\'m here-\nAn arm pulls at him- the Neighbor.\nNEIGHBOR\nGet the fuck out of here...\nBen turns and pushes the Neighbor back. He hits the dirt outside. Marie screams again.\nBEN\nIt\'s Ben! It\'s me!\nShe begins to scrabble backwards as... JACK runs into view to protect her.\nJACK\nStay away from her. Stay away!\nBen halts.\nBEN\nJack...\nMarie grabs Jack and lifts him up.\nMARIE\nI don\'t know who... what you are-\nJACK\nHe looks like Daddy!\nMARIE\nIt\'s not Daddy, it\'s not Daddy-\nBEN\nIt *is *me, it\'s Daddy, it\'s Ben-\nThe Neighbor pile-drives Ben against the wall, bear-hugging him. Ben struggles with the Neighbor, getting him off as...\nSiren lights flood the room. Cop cars. Casting light on-\nAn IN MEMORIAM PHOTO on the wall of Ben. Ben finally begins to realize... looking at Marie and Jack, both horrified.\nMARIE\nWe saw him die. We saw him-\nBen comes to his senses. He breaks from the Neighbor as-\nCOPS barrel through the front door to find...',
    '177': 'EXT. NEIGHBOR\'S GARDEN - DAY #177#\nBen scrabbling over a fence into a neighboring garden. He can see TWO CRUISERS parked out front.',
    '178': 'EXT. SIDE OF NEIGHBOR\'S HOUSE - CONTINUOUS #178#\nBen makes his way down the side of the house. Reaching...',
    '179': 'EXT. SUBURB - CONTINUOUS #179#\nBen makes for his pickup. Keys out.\nBen drives into a U-turn.\nREARVIEW: Cops haven\'t seen him.\nHe accelerates...',
    '180': 'EXT. SCENIC PULLOUT - DAY #180#\nThe pickup pulls into a space, shaded away from the road.',
    '181': 'INT. PICKUP - DAY #181#\nBen kills the engine. He puts his head in his hands.\nA siren sounds...\nHe listens as it gets closer... blue and red siren lights sweep the car...\nThe police car keeps going.',
    '182': 'INT. PICKUP (MOVING) - FRONT YARD - DAY #182#\nBen drives toward the cabin and parks.\nThe door to the cabin is open.',
    '183': 'EXT. FRONT YARD - CABIN - DAY #183#\nBen takes out the revolver. Checks the cylinder. Six shells. He snaps it shut, moves quickly up the porch.',
    '184': 'INT. LODGE ROOM - CABIN - DAY #184#\nBen comes in hot, finding-\nWaif, sitting on the couch, smoking, watching TV. IT\'S A WONDERFUL LIFE plays. Jimmy Stewart is staggering through snow on a bridge, lost and drunk.\nWaif doesn\'t look up. He slides the gun into his waistline.\nWAIF\nHow was the reunion?\nBEN\nThey thought *I was dead*.\nWAIF\nReally. Can I ask, was the car there?\nHe says nothing.\nWAIF\nWas the car there? You said it was a jumper, right? Totaled your car?\nBEN\nNo... no, it was a new car.\nWAIF\nOK. Well that makes sense.\nHe moves towards her and slaps her cigarette.\nBEN\n_They thought I fucking died_.\nWAIF\nBecause *you did *die, dummy.\nShe sits up and taps out the lit cigarette with her foot. She takes out another cigarette tentatively.\nWAIF\nCan I light another... or you going to keep showing me the rough stuff?\nHe says nothing. She lights it.\nWAIF\nThe car wasn\'t there. That\'s what makes sense. I guess someone still lands on it then... except this time, you\'re catching it, not your kid.\nBEN\nThis isn\'t what I wanted-\nWAIF\nBaby steps. What you want to find, is the car in the driveway, OK? The same car. That way, you know the accident didn\'t happen.\nBEN\nThings are different here. It\'s not just the accident happened differently, it\'s other things.\nWAIF\nImagine you\'re on a highway and you\'re speeding toward a wreck. What happens? You swerve, you survive, but now you\'re in another lane. Course things are going to be different. A world where your son is still alive... is different by definition. It can\'t have followed the path you\'re on. But if you do it again...\nShe reaches for the revolver. He steps away.\nBEN\nI\'m not doing this again...\nWAIF\nIf you do it again... maybe the jumper catches asphalt and not you. Or your kid.\nBEN\nNo. No way.\nWAIF\nThen get used to the rest of your life. Good luck getting the social security re-activated.\nHe walks onto the porch. She stands to follow him.\nWAIF\nYou know there\'s a world out there where you can be with them. And yeah there\'ll be... differences. The  trick is getting close enough, so you can live with the differences. You imagine hugging your son again Ben?\nShe gets up and moves toward him. She pulls the gun from his waistband gently.\nWAIF\nYou want to be with him?\nBEN\nNot like this. This is... I don\'t know what this is.\nWAIF\nIt\'ll tell you what it is. It\'s easy as changing a suit.\nShe puts the gun in his hands.\nBEN\nYou do it.\nWAIF\nDoesn\'t work that way. The observer has to be the one to pull the trigger. To make the choice.\nWAIF\nYou\'ve come this far. One more time... maybe you\'ll be together.\nOr maybe you want to give up on them. Accept who you are. Where you are.\nHe looks at the revolver. Deciding.\nBEN\nFuck it.\nSticks it to side of his head.\nPulls the trigger-\nIt fucking FIRES.\nBen drops. Head hits wood.\nWaif looks down at him... then steps over his body and returns her seat. Pulls her legs up and watches the TV.\nCUT TO:\nEND CREDITS of a film begin to play.\nWe pull out... through the glass to reveal...',
    '185': 'INT. LODGE ROOM - CABIN - DAY #185#\nThe credits play on the TV.\nBen lies on the ground. The gun is at his side. Not moving.\nBen comes to with a start. He is alone. The door is open. The revolver by his side on the floor.\n!IN A MIRROR:\nBen checks his face. A scorch mark and welt. Misfire.\nBen inspects the gun. Opens the cylinder. Five rounds left.\nHe puts it back in the rosewood box... notices the bookcase is full. Marie\'s books.\nHe looks around. The place looks undamaged and lived in. Everything he packed is in its original place.\nHe moves to the wall... pulls down something pinned to it. It is a crayon drawing in Jack\'s style.',
    '186': 'EXT. CABIN - DAY #186#\nBen steps outside.\nThe Subaru Forester is parked up the driveway. _Undamaged_.',
    '187': 'EXT. FOREST - DAY #187#\nBen moves through a forest path... frantic... searching...',
    '188': 'EXT. LAKESIDE - DAY #188#\nBen pushes through overgrowth, opening up to the lakeside.\nA mother and child are at the shore, backs to him. The child scoops water into a bucket.\nBEN\nHey.\n_MARIE_ turns to look at him. The scene is ethereal, with light dappling on the water.\nMARIE\nWhat happened?\nBen says nothing.\nMARIE\nYour head.\nHe raises a hand to touch his forehead.\nBEN\nSlipped in the cabin. Caught my head.\nMARIE\nIt looks burnt-\nBEN\nIt\'s fine, it\'s fine. Everything is fine.\nBen kneels beside Jack. The lake is suffused with light.\nBEN\nWhatcha doing?\nJACK\nCatching fish.\nJack tips the bucket back into the water and fish wriggle away. Ben gently puts an arm around Jack.\nMarie watches. Something is off.',
    '189': 'EXT. FOREST TRAIL - DAY #189#\nMarie and Ben walk uphill. Jack is on Ben\'s shoulders. Schrödinger creeps out, weaving between Ben\'s legs.\nMARIE\nSeems like it knows you.\nBEN\nWe should hit the road, right?\nMARIE\nYeah. Lets get packed up.\nBen nudges Schrödinger away with his foot. It hides in the undergrowth and watches the family as they move.',
    '190': 'INT. LODGE ROOM - CABIN - DAY #190#\nJack comes inside and sits on the sofa. He turns on the old television.\nMARIE\nGo inside Jack. Read a book.\nJACK\nI don\'t want to.\nMARIE\nDo what I say Jack. Go in.\nJack mopes inside. She shuts the door. Marie turns on the television. She turns up the volume. Then she turns to Ben.\nMARIE\nI know what the mark is.\nBen says nothing.\nMARIE\nDon\'t get angry.\nBEN\nI\'m not angry-\nMARIE\nBut what the fuck is wrong with you? Is that what you want for Jack? To come back here and find you on the floor... like that?\nHe says nothing.\nMARIE\nAre you using again?\nBEN\nNo... of course not. Absolutely not.\nShe looks around the space.\nMARIE\nI just want to get out of here.',
    '191': 'EXT. FRONT YARD - CABIN - DAY #191#\nJack carries a small travel bag, and pads to the Forester outside. Marie follows with a small luggage case.',
    '192': 'INT. LODGE ROOM - CABIN - DAY #192#\nJack looks around the cabin. He sees the rosewood box. He picks it up...\nSets it under the bed. Then leaves.',
    '193': 'INT. FORESTER (MOVING) - BROKEN BOW - DAY #193#\nMain Street rolling past Ben\'s window.\nSame street, different feel. Neon signs for cannabis outlets. A woman pushing a can trolley.\nMarie observes Ben. His circular burn mark.\nMARIE\nYou\'re acting kinda strange.\nBEN\nYeah. I know. I just had this... this bad dream, back at the cabin. Takes a little bit of time to shake.\nHe pulls up outside...\nT.R. GENERAL SUPPLIES',
    '194': 'INT. T.R. GENERAL SUPPLIES - DAY #194#\nBen enters. Adjusts as the store layout is different again. No guns, but a large vape stand instead. Behind the counter is "T.R." (50s, wiry)\nBEN\nWhere\'s Jamie?\nT.R.\nWho you talking about?\nBEN\nThe owner.\nT.R.\nOh no man... he sold me this place years ago. After his son died.\nT.R. moves to a backroom, leaving Ben alone.\nBen picks up a set of waters. Turns a corner to find-\nWaif. In shades and hat, tags still on both.\nWAIF\nIt\'s not that easy...\n(switches glasses)\nBen turns away from her and goes to the counter.\nWAIF\nGonna pretend we don\'t know each other.\nBEN\nYou\'re not a part of my life... not this life. Let\'s leave where it was.\nWAIF\nWould it were, or could it be so.\nBEN\nI don\'t know what that means.\nBen pushes the bell for service.\nWAIF\nWe\'re entangled. My world and yours, all wrapped up together. We share the same state.\nBen pushes the bell again.\nWAIF\nYou think you can protect them?\nHe turns to her. Quietly serious.\nBEN\nYou stay away.\nWAIF\nNot me you have to worry about.\nT.R. appears from the backroom.\nT.R.\nCash only.\nBen fishes in his pocket. Waif exits.\nBen watches her through the doorway. She passes the Forester, where Marie follows her path.',
    '195': 'INT. FORESTER (MOVING) - BROKEN BOW - DAY #195#\nBen drives. Marie is looking at him.\nMARIE\nWho was that?\nBEN\nJust a local. A tweaker.\nMARIE\nYou know her?\nBEN\nI\'d think I\'d remember. You want to get lunch-\nMARIE\nYou serious? You know there\'s nowhere good to eat here anymore. We\'ll cook at home.',
    '196': 'EXT. HIGHWAY - DAY #196#\nThe Forester driving south.',
    '197': 'EXT. SUBURB - DAY #197#\nThe Forester driving through the neighborhood.',
    '198': 'INT. FORESTER (MOVING) - SUBURB - SUNSET #198#\nBen observes the neighborhood. It is more rundown than when we last saw it. Boarded up houses. Overgrown gardens and for sale signs. Like an exodus.\nFinally... reaching his house.',
    '199': 'EXT. SUBURBAN HOME - SUNSET #199#\nBen pulls the Forester alongside the Ford F-250.\nSteps out, as Marie gets Jack out of the car.\nBen tries his keys with trepidation... the door unlatches. The keys work. He opens the door and Marie steps past him. Ben ruffles Jack\'s hair as he passes.',
    '200': 'INT. HALLWAY - HOUSE - NIGHT #200#\nBen navigates the landing. Everything mirrored, reversed. Marie appears in the kitchen doorway.\nMARIE\nI\'m cooking pasta sauce.\nBEN\nOK.\nHe reaches for her. She flinches at his touch.\nBEN\nAre you OK?\nShe nods. He pulls her close, holds her. Smells her skin. She adjusts to his gentleness and sinks into the embrace.\nA kitchen alarm. She breaks away.\nMARIE\nI have to turn down the heat.\nShe goes into the kitchen, adjusts the stove top.',
    '201': 'INT. HOUSE - FIRST FLOOR - NIGHT #201#\nBen climbs the stairs. Everything seems backward. Jack\'s room is on the opposite side. A thin light from beneath the doorway.\nBen listens at the door. The sound of a game.',
    '202': 'INT. JACK\'S ROOM - HOUSE - NIGHT #202#\nJack is knelt in front of a screen playing a computer game.\nBEN\nHey.\nJack pauses the game and looks at the floor.\nJACK\nI\'ve done my homework. So I can play, right?\nBEN\nSure you can.\nBen steps toward Jack, who turns... edgy. Ben scruffs his hair. Jack is uncertain about the affection. Tense.',
    '203': 'INT. KITCHEN - HOUSE - NIGHT #203#\nTomato sauce poured over pasta. Ben and Jack sit as Marie serves. She sits. Neither mother nor son lift a fork. They wait, as if disciplined.\nMARIE\nYou going to say grace?\nBen puts his hands together. No idea what to say.\nBEN\nI\'d like us to be thankful... for the meal before us. For us being here. All together. Safe and well. OK. Eat.\nMarie and Jack stare at Ben, then glance at each other. They begin to eat.',
    '204': 'INT. LIVING ROOM - HOUSE - NIGHT #204#\nBen looks at the mantelpiece. Pictures from the past -strange and formal. He picks one up; Marie and him.\nBEN\nI forget... where was this taken?\nShe looks at it.\nMARIE\nOutside Northrup. Where you asked to marry me.\nThe landline rings. Marie picks up.\nWAIF (O.S.)\nHey... is Ben there?\nMARIE\nWho is this?',
    '205': 'EXT. HOUSE - NIGHT #205#\nWaif is shadowed on the driveway. Watching Marie.\nWAIF (O.S.)\nOld friend. Me and Ben go way back.',
    '206': 'INT. LIVING ROOM - HOUSE - NIGHT #206#\nMarie brings the phone to Ben.\nMARIE\nSomeone for you.\nBEN\nWho?\nShe says nothing. He takes the receiver.\nBEN\nWho is this?\nWAIF (O.S.)\nI know you don\'t want to talk... but there are things you need to know. About what\'s going to happen.\nBEN\nSorry, I didn\'t catch your name.',
    '207': 'EXT. HOUSE - NIGHT #207#\nWAIF\nOK, man. Play it your way.\nSee you real soon.\nWaif folds the phone and walks away.',
    '208': 'INT. LIVING ROOM - HOUSE - NIGHT #208#\nBen hands back the receiver.\nMARIE\nWho was that?\nBEN\nDon\'t know. Crank.\nMARIE\nShe said she knew you-\nBEN\nThey ring again, hang up.\nMARIE\n(quiet)\nAre you keeping things from me?\nBEN\nLet\'s talk later...\nHe directs her toward Jack, who is watching.',
    '209': 'INT. JACK\'S BEDROOM - HOUSE - NIGHT #209#\nA blanket fort has been set up in the room.\nInside, Ben is hunched next to Jack, lit by a toy lamp.\nBEN\n(reading)\nShe was forced to dance... over fields and meadows ... in rain and in sunshine, by day and by night. It was most dreadful.\nJACK\nWhat does dreadful mean?\nBEN\nMeans really bad. Something awful.\nJACK\nOK. You can keep going.\nBEN\nShe danced over the churchyard, though the dead did not dance... they had better things to do.\nCUT TO:\nBen lifts sleeping Jack to bed. Tucks him in.\nThrough the open door, Marie watches.',
    '210': 'INT. MASTER BEDROOM - HOUSE - NIGHT #210#\nBen shuts the door.\nMarie undresses, watching him. He turns the lights off. In the moonlight he watches her strip to her underwear.\nHe takes off his clothes. She gets into bed and lays quite still on her back. He climbs in beside her.\nHe touches her skin, gathering familiarity again. An old bruise on her shoulder.\nBEN\nHow did you get that?\nMARIE\nYou know how I got it.\nHe moves his hand between her legs. She responds to his touch. Looking at him. Angry but turned on.\nHe moves on top of her. Holding her wrists.\nFirelight dances across her face. Ben looks to the window. He gets out of bed and moves to the glass.\nMARIE\nWhat is it?\nBEN\'S POV: The bushes in their garden are on fire. The same pale fire we saw earlier.\nThe Mother is silhouetted by the flames, moving her hands and lips in some silent incantation... she stops.\nMarie joins Ben at his side.\nMARIE\nWho the hell is-\nThe Mother\'s head snaps forward. Marie gasps and backs away.\nMARIE\nI\'m calling the police.\nBen watches as...',
    '211': 'EXT. FRONT LAWN - HOUSE - NIGHT #211#\nGrimy feet walk toward the fire and The Mother. We drift up to bony ankles to catch the trails of a white dress.',
    '212': 'INT. MASTER BEDROOM - HOUSE - NIGHT #212#\nBEN\'S POV: A pale ethereal figure joins the side of The Mother to be lit by flame-light. This is the LADY IN WHITE.\nMarie with phone to ear.\nMARIE\nNot picking up.\nThe two figures now SWEEP the house... falling into shadow and out of view.\nBEN\nThey\'re coming.\nMARIE\nWho?\nBen moves to the door... and...',
    '213': 'INT. JACK\'S ROOM - HOUSE - NIGHT #213#\nBursting in, waking Jack in fright. Ben scoops him out of the bed.\nBEN\nIt\'s OK buddy, it\'s OK.',
    '214': 'INT. LANDING - HOUSE - NIGHT #214#\nBen carries Jack toward the master bedroom.\nBANGING downstairs. The front door getting smashed in.',
    '215': 'INT. MASTER BEDROOM - HOUSE - NIGHT #215#\nBen sets Jack down in front of Marie. She is clutching the phone desperately.\nMARIE\nI can\'t get through... I can\'t get through.\nBen takes the phone out of her grasp, and holds her face.\nBEN\nLook at me, OK?\nThe sounds downstairs grow louder.\nBEN\nOnce I go outside... you keep that door shut. You push the bed against it, anything else you can. Blockade yourself. Don\'t let anyone in. OK?\nShe nods and braces herself.\nCUT TO:\nBen and Marie push the bed toward the door. He leaves a gap large enough to squeeze through.\nBEN\nDon\'t open it for anyone.\nHe rushes out and she shoves the bed against the door.',
    '216': 'INT. STAIRCASE/GROUND FLOOR - HOUSE - NIGHT #216#\nBen flies downstairs two steps at a time. Glass crunches underfoot, shards glittering in the moonlight.\nHeavy fists pound the door, smashing the wood.\nHe slaps the light switch - lights flicker and fade. Something *humming* in the walls.\nBen cuts across the hall, opening a door to find... a pantry. Everything reversed. He crosses the hall and enters:',
    '217': 'INT. BASEMENT - HOUSE - NIGHT #217#\nLight spilling in, broken by Ben\'s silhouette.\nHe makes his way uneasily downstairs, moving into darkness. He grabs a flashlight, lights the way.\nHe reaches a wall of tools hung from nails. He scans for...\nAN AX. Takes it down.\nSees a roll of duct tape... quickly pulls a stretch loops the flashlight to the ax handle.\nHe grabs a box-cutter and sticks it in his back pocket.\nHe begins to climb the stairs, ax pointed ahead of him like a sword, as he directs the light cone from the flashlight.',
    '218': 'INT. HALLWAY - HOUSE - NIGHT #218#\nBen arrives into the hallway.\nHe sweeps ax left, right - clear.\nLeft again-\n_THE MOTHER is suddenly upon him_.\nShe grabs the ax handle with both hands and smashes him into a wall as he grips it. She pushes the handle against his neck and lifts him off the ground.\nBen desperately tries to pull the ax down, looking to see:\nBEN\'S POV: The Woman in White begins to move up the stairs.\nBen lets go of the ax, being choked more. He reaches down for his pocket... trying to get the box-cutter... not quite reaching...',
    '219': 'INT. MASTER BEDROOM - HOUSE - NIGHT #219#\nMarie pushes the bed against the door. Jack tries to help but he\'s just a kid.\nThe door THUMPS. It THUMPS AGAIN, and AGAIN, and AGAIN. The hinges begin to give way.',
    '220': 'INT. CORRIDOR - HOUSE - NIGHT #220#\nBen\'s hands struggle to reach the box-cutter. He can hear the THUMP from upstairs.\nHis finger grabs the box-cutter, feeding them into his grip.\nHe JABS and SLICES The Mother\'s face. Her skin breaks like pieces of tough paper... her strength fading as she falls.\nBen rips the ax from her grasp.\nHe swings it into her side. She folds and buckles unnaturally. A *hissing sound *like a burst balloon.',
    '221': 'INT. MASTER BEDROOM - HOUSE - NIGHT #221#\nAnother THUMP shakes Marie, whose back is pressed against the bed frame. The plaster and hinges giving way...\nIt stops.\nShe looks over her shoulder, uncertain.\nShe moves up to the door. She places her head against it to listen...\nThe plaster EXPLODES and A WIRY HAND grabs at her... Marie SCREAMS and tries to break free.\nThe Woman in White begins to smash through the wall plaster.',
    '222': 'INT. CORRIDOR - HOUSE - NIGHT #222#\nBen hearing the scream... swinging another blow against The Mother. She buckles again and whines, sinking to the ground.\nBen moves toward the staircase-\nFALLING FORWARD. He looks back to see his ankle has been grabbed by The Mother.\nHe tries to free himself. The flesh on his ankle is torn by her close grip.',
    '223': 'INT. MASTER BEDROOM - HOUSE - NIGHT #223#\nMarie holds Jack in the corner in terror, watching as:\nThe Woman in White begins to unnaturally squeeze through the gap... resuming shape as she gets into the room.',
    '224': 'INT. CORRIDOR - HOUSE - NIGHT #224#\nBen is still trapped by The Mother\'s grasp.\nHe slams the ax into The Mother\'s wrist... it wilts and withdraws. The sound of rushing air and a crackle of static.\nBen gets to his feet... limping, blood spilling onto the floor from his ankle wound.\nHe gets to the door and sees..\nThe legs of The Woman in White, squeezing through the hole are dangling in the air...\nBen runs forward and HACKS at them.\nSmash, smash, smash... they drop off, devoid of matter.\nThe Woman In White slips inside the hole...',
    '225': 'INT. MASTER BEDROOM - HOUSE - NIGHT #225#\nMarie and Jack back away.\nThe Woman in White orients herself on her elbows. Drags herself forward, insect-like.\nBehind her, Ben is looking through the hole. He moves sideways and-',
    '226': 'INT. CORRIDOR - HOUSE - NIGHT #226#\nBen slams his shoulder against the door, again, and again.\nHe picks up the ax and begins to smash open the hole more.\nAs he breaks it through he sees The Woman In White get closer to Jack and Marie.',
    '227': 'INT. MASTER BEDROOM - HOUSE - NIGHT #227#\nBen pushes the ax through the hole in the wall and squeezes through. He lands on the ground as-\nThe Woman In White reaches for Jack - held back by Marie. Long hands stretching and reaching for his neck as-\nAn ax splits her head, held by-\nBen who yanks it back. Pulls her body with him.\nHe yanks it out and swings sideways into her.\nHer LEFT ARM becomes half-severed, hissing with air.\nHe slams the ax into her legs, crippling her. The Woman In White lands on the ground... crawling toward him until he...\nCaves her head in. Empty space and dusty.\nBen moves to Jack and Marie.\nBEN\nAre you OK? Is Jack OK?\nThey are numb. In shock.\nBEN\nWe have to get to the car, OK?\n(off Marie)\nMarie, respond to me. We need to get to the car. Say it back to me.\nMARIE\nWe need to get to the car.\nShe lifts Jack into her arms. Ben removes the blockage from the door... picks up the ax...',
    '228': 'INT. DOWNSTAIRS - HOUSE - NIGHT #228#\nBen leads with the ax. Marie with Jack behind him.\nHe stops - The Mother on the ground, *reassembling *herself. Severed parts inflating and locking together.\nBEN\nStay close to me.\nBen moves to the front door, swinging it open.',
    '229': 'EXT. FRONT LAWN - HOUSE - NIGHT #229#\nBen moves to the Forester, followed by Marie and Jack. Lit by the pale blue firelight.',
    '230': 'INT. FORESTER - NIGHT #230#\nBen turns keys in ignition. A dry CLICK.\nBEN\nOh shit.\nHe tries a few more times as Marie puts Jack in the back, seat-belting him.\nBEN\nIt\'s not starting. Where\'s the keys to the Two-Fifty?\nMARIE\nI saw them in the bedroom.\nBen gets out and moves toward the house when-\nThe Mother appears at the doorway. Assuming a more rigid and upright form, staccato movements becoming smoother.\nBen opens the back door. Holds out the ax.\nBEN\nI need to fix the car.\nShe takes it and forms a barrier between The Mother and Jack. Ben reaches into the cab, grabs a screwdriver and a length of spare wire from behind the seat.\nBen pops the hood.\nThe Mother staggers toward Marie. She wields the ax.\nMARIE\nStay the fuck away- stay away!\nUnder the hood; Ben\'s eyes the starter solenoid. The terminals are blackened. One lug blistered .\nThe Mother lurches toward Marie.\nMarie swings, catches her arm... the ax is STUCK in the arm.\nBen twists copper over the terminals. Bridges the battery post to the starter terminal with the screwdriver tip—\nSPARK. The engine starts.\nBen turns and races toward The Mother. He wrenches the ax free and hacks at her.\nJack watches from inside the car.\nBen swings the ax into The Mother\'s side - winding her, air hissing out, her form buckling to reveal...\nThe Woman in White pushing through the door. Scrabbling toward Ben on four legs.\nMarie in the driver\'s seat - pushes open the passenger door.\nMARIE\nGet in!\nINT./EXT. FORESTER - NIGHT #231#\nBen gets in and Marie floors the accelerator as-\nThe Woman In White traps her hand in the open doorway. Ben slams the door again.\nThe hand breaks at the wrist and the woman in white tumbles away. He opens the door and kicks the twitching hand to the roadside.\nMarie drives fast.\nMARIE\nWe need to get to the police. Call them.\nShe tosses Ben her phone. He opens it to dial, then- stops.\nBEN\nThey can\'t help us.\nMARIE\nOf course they can help us, those... people, those-\nBEN\nThose things weren\'t people.\nBEN\nListen... we end up in a police station... they could corner us. We could be trapped.\nMARIE\nWhat do we do? Where do we go then?\nBEN\nThe cabin.\nMARIE\nAre you crazy-\nBEN\nIt\'s secure. I\'ve got the gun. It\'s home turf.\nThere\'s a sound of sobbing. Ben turns to see Jack is crying.',
    '232': 'EXT. HIGHWAY - NIGHT #232#\nThe Forester turning off the main highway.',
    '233': 'EXT. FRONT YARD - CABIN - NIGHT #233#\nThe Forester pulls up outside the cabin.',
    '234': 'INT. / EXT. FORESTER - NIGHT #234#\nBen gets out and scans the surroundings.\nBEN\nKill the engine.\nMarie turns it off.',
    '235': 'EXT. FRONT YARD - CABIN - NIGHT #235#\nBen listens to the sound around him. Scanning the forest and the perimeter.\nBEN\nI\'ll check inside. If there\'s any problems -\nMARIE\nWe take off.\nBen moves to the cabin door quickly. He undoes the locks, one at a time. Pushes inside.',
    '236': 'INT. LODGE ROOM - CABIN - NIGHT #236#\nIt is dark and empty. He turns the houselights on. The place seems undisturbed.',
    '237': 'INT. KITCHENETTE - CABIN - NIGHT #237#\nBen opens the door. Scans everything. Checks the back door-\nUnlocked.\nHe locks it. Swings down the shutters.',
    '238': 'EXT. FRONT YARD - CABIN - NIGHT #238#\nBen carries the ax and Marie carries Jack.',
    '239': 'INT. LODGE ROOM - CABIN - NIGHT #239#\nBen locks the door as Marie pulls up the shutters. Ben reaches under the bed and reaches for the rosewood box.\nHis hand finds space. It\'s not there.\nHe looks under. He grabs the electric lamp and cast it under to try to find it.\nBEN\nWhere\'s the gun?\nMARIE\nI didn\'t move it.\nBEN\nJack, did you move a box of mine?\nJack says nothing. He is sitting on the ground rocking. Ben looks around... moves toward Jack\'s room.\nThe door opens.\n_Waif_ steps out.\nWAIF\nHey Ben.\nMarie rushes to grab Jack, pulling them to a corner of the room and grabbing ax.\nWAIF\nI\'m not gonna hurt you. I\'m a friend, right?\nMARIE\nWho the fuck is she Ben?\nWAIF\nYou want to tell her or will I?\nBen keeps his body between Waif and his family.\nBEN\nJust some nutcase, OK? She came to the cabin looking for help, I let her in, she must have stolen a key-\nWAIF\nThat\'s not very nice Ben.\nBEN\nWhere\'s the fucking gun?\nWaif pulls it from her rear waistband. Dangles it loosely.\nMARIE\nOh God, oh god-\nShe hands it over. Ben grabs it and turns it on her.\nWAIF\nYou gonna shoot me Ben?\nJack looks at Ben pointing the gun at her. He feels shame. Lowers it.\nWAIF\nThe truth is Ben... we\'ve known each a long time. Much longer than these appendages.\nBEN\nShut up, you crazy liar-\nWAIF\nDon\'t you remember what our life used to be?\nBEN\nWe haven\'t had a life...\nWAIF\nOh we\'ve had many. But do you remember the first one? When things got bad, what we\'d do for money?\n!FLASH-\nBen with Waif holding up a liquor store.\nGuns and chaos and masks.\n!PRESENT-\nOn Ben, remembering, or trying not to.\nWAIF\nWe moved up, we\'d rip off big homes looking for scores.\n!FLASH-\nBen and Waif in a convertible, eyeballing the mansion.\n!PRESENT-\nWAIF\nWe cased the place two days. Thought the old doll lived alone.\n!FLASH-\nMasked Waif SMACKS Joyce in the mouth with a cosh. Ben binds her hands as...\nThe basement door opens and QUERRY PRESTON emerges. He is holding the revolver.\n!PRESENT-\nWAIF\nHe pulls the piece and aims it at us. I thought that was it.\n!FLASH-\nQuerry turns the gun from them to his own head. He pulls the trigger. Nothing happens. He pulls again, and again-\n!PRESENT-\nWAIF\nYou didn\'t think it was loaded.\nBEN\n(remembering)\nIt couldn\'t be loaded.\n!FLASH-\nBen twists Querry\'s wrist, snapping it. Taking the gun.\nBEN\nRemember to load it next time, pops-\nThe gun fires- blowing Querry\'s brains out. Joyce screams.\n!PRESENT-\nMARIE\nWhat is she talking about? *Who are you?*\nBEN\nListen, this isn\'t true, what she\'s saying is not true-\nWAIF\nWho planted the gun here?\nHaven\'t you worked it out yet?\nBEN\nSomeone put it under the floorboards.\nWAIF\nNext to bad wiring you\'d have to fix. Someone who knows you pretty well. Like yourself.\n!FLASH-\nPanels are lifted by Ben.\nRosewood box is jammed against the electric run.\n!PRESENT-\nWAIF\nSo you\'d find your way back to me.\nBEN\nNo, no, no- I\'d remember, I\'d remember this... I\'d remember it clearly...\nWAIF\nThat\'s the tricky part. You can remember not doing it... but you can also remember doing it, can\'t you. You\'re all of a jumble. You know how many lives we\'ve led? Round and round and round.\nMarie stands and yanks Jack\'s arm, moving to the door.\nMARIE\n(to Ben)\nI don\'t know who you are. I don\'t know what this is but I want to get out of here-\nBEN\nSIT BACK DOWN!\nHe points with the gun. He realizes he\'s scared them.\nBEN\nJust sit down, OK? It\'s not safe out there. You\'re safe here. I can protect you, I can protect both of you. That\'s all I want.\n(...)\nPlease. Sit.\nMarie and Jack - both frightened - sit on the sofa.\nWAIF\nThey weren\'t meant to be.\nWe\'re what\'s meant to be.\nHe turns the revolver on Waif.\nBEN\nYou need to shut up.\nWAIF\nWhat are you gonna do? Shoot me, Ben?\nShe exposes her breastbone.\nWAIF\nDo it right here. Do it right into the heart.\nShe steps toward him until her chest presses the nozzle.\nWAIF\nI\'ve tried to forget you... and you\'ve tried to forget me. But however fast we run from each other we end up back in the same spot. We\'re entangled. Twin-souls. Two twists of fate, coiled together. Around and around we go.\nShe looks over his shoulder at Marie.\nBEN\nI don\'t even know your name.\nWAIF\nYou didn\'t ask... \'cos you already know it. And I\'ve had a lot of them. Alice. Mary. Junebug. Kit... just like you Benny. Many names. Many lives. A carousel, looping round and round-\nThe houselights flicker off. Pale blue flamelight is visible through the gaps in the shutters.\nIn the confusion Waif steps forward and kisses Ben. He pushes her away as she smiles in delight and the houselights come on again.\nWaif turns her attention toward Marie and Jack.\nWAIF\nI\'m not gonna hurt you...\nBen moves to the window. He looks out to see:\nThe Mother. The Woman in White. And... OTHERS... similar forms, faces flickering in the pale light.\nWAIF\n*...\'*cos* they\'re* gonna hurt you.\nBen goes to the desk. He scoops bullets out of the ammunition box and stuffs them inside his jacket pocket.\nHe pulls Waif toward the Kitchenette.',
    '240': 'INT. KITCHENETTE - CABIN - NIGHT #240#\nBen pushes her inside. Wielding the gun. No one there. No shadows at the window.\nBen turns on a burner. Sets a saucepan down. Empties in a bottle of olive oil.\n!SMASH—\nLodge room window.',
    '241': 'INT. LODGE ROOM - CABIN - NIGHT #241#\nA HAND is pushing through the shutters. Ben sticks the revolver right in the palm.\nBlows it away.\nAnother HAND smashes through a window. Marie chops at it with the ax.',
    '242': 'INT. KITCHENETTE - CABIN - NIGHT #242#\nBen lifts the pan of boiling oil and slings it out the window. An OTHER howls and retreats... he sees a glimpse of the Other\'s face melting.',
    '243': 'INT. LODGE ROOM - CABIN - NIGHT #243#\nBen fires the gun through the slats.\nCatching an eyeball. Watching an Other spin and hit the dirt. A skull cracking open, exposing hollowness.\nHe turns to see Waif go into the kitchen.',
    '244': 'INT. KITCHENETTE - CABIN - NIGHT #244#\nBen comes inside to find... Waif has opened the backdoor, and an OTHER pours through the door. It passes right by her and streams toward Ben.\nHe grapples with the Other, falling backward into-',
    '245': 'INT. LODGE ROOM - CABIN - NIGHT #245#\nBen is mounted by the Other. He looks to Marie.\nBEN\nGet the ax\nShe picks it up and comes toward him... then looks to the door. She scoops up Jack and runs to the door.\nMARIE\nI\'m sorry, I\'m sorry I can\'t-\nBEN\nMarie! Don\'t! Don\'t go out there!\nThe Other snarls on top of Ben, head snapping forward, trying to bite.\nMarie gets the door unlocked. She carries out Jack into the blue-flame tinged darkness.\nBEN\nMarie!\nBen reaches up and puts on hand on the Others jaw and the other hand on its forehead... he pulls, snaps its neck.\nHe throws the Other off and rushes toward the door.',
    '246': 'EXT. FRONT YARD - CABIN - NIGHT #246#\nMarie running past the grappling hands with Jack. Hands on keys... reaching the car... getting Jack inside and slamming the door just as-\nHANDS slam against the glass, trying to get her.',
    '247': 'INT. LODGE ROOM - NIGHT #247#\nBen runs with ax to the doorway when-\n_The Mother_ blocks his path. Hissing at him. Arms holding onto the frame.\nHe swings an ax into her arm, bisecting it as-\nAn OTHER attacks him from behind, choking him.',
    '248': 'INT. PICKUP - NIGHT #248#\nMarie gets the lights on, revealing-\n_The Woman In White_ on the hood, crawling up it like a spider. She turns the key to the engine.\nCLICK. CLICK. It won\'t start-\nMARIE\nPlease, please, please-',
    '249': 'INT. LODGE ROOM - CABIN - NIGHT #249#\nBen smashes the butt of the ax into the Others head. It crumples like an eggshell, vaporous and hissing.\nHe turns and SMASHES the ax through The Mother\'s other arm. He HACKS at her legs like they are vines.',
    '250': 'INT. PICKUP - NIGHT #250#\nThe Woman In White crawls up to the roof of the car.\nJACK\nMom! I\'m scared!\nMARIE\nIt\'s OK, it\'s OK-\nShe tries the engine again and again-\nSMASH - a hand tears through the roof. Sharp elongated fingers, reaching for Jack.\nMarie reaches back and tries to protect Jack.',
    '251': 'INT. LODGE ROOM - CABIN - NIGHT #251#\nBen SMASHES the ax across the neck of The Mother. She wilts and he pushes past her...',
    '252': 'INT. PICKUP - NIGHT #252#\nThe Woman In White smashes another hand through the roof. Long razor-like fingernails slicing through the aluminum.\nIt pierces Marie\'s side. She screams and rips herself free.\nThe roof begins to sag and crumple inward, as if The Woman In White is gaining mass. The space begins to collapse, entombing Marie and Jack...',
    '253': 'EXT. FRONT YARD - CABIN - NIGHT #253#\nBen hacks his way through Others, lit by the blue flames from the fire pit. He smashes his ax into their ankles, flips the ax onto the handle and smashes them away, racing toward-\nThe F-250 Pickup. The roof caved in. The figure of the Woman in White draped across it like some strange expressionist sculpture.\nBEN\nNo, please no, please no...\nHe smashes the ax down on her arms. Cutting through and maiming her. She slips off the car like a fattened predator, heavy of belly.\nBen pushes through the gap. Inside:\nMarie is dead. Face blank. Sides ripped out. Still holding...\nJack. Half his head missing, like the accident.\nBen begins to scream.',
    '254': 'INT. LODGE ROOM - CABIN - NIGHT #254#\nWaif watches from the doorway. Covered in blood. She lights a cigarette casually.',
    '255': 'EXT. FRONT YARD - CABIN - NIGHT #255#\nThe Others begin to move toward Ben. Preservation kicks in. He picks up the ax to face them... bracing...\nHe stops. Drops the ax to his side. Closes his eyes.\nThe Others rush him. The reassembled MOTHER gets in his face. The OTHERS circle around him like hyenas.\nHe opens his eyes. Looking at The Mother. The Others. They won\'t touch him. Like he\'s poisoned meat. Inedible.\nThey move away from him and form two lines, leading toward the cabin. Now Ben can see Waif standing in the doorway, back-lit by the light inside.\nHe steps forward and walks slowly through the gauntlet that has formed. Blank faces, all tracking him.\nAs he nears the cabin and Waif withdraws inside.',
    '256': 'INT. LODGE ROOM - CABIN - NIGHT #256#\nBen steps inside. He looks back at the faces, lit by the pale fire.\nBEN\nHe\'s gone. She\'s gone.\nWAIF\nYeah.\nBEN\nBut they didn\'t take me. Why didn\'t they take me?\nWAIF\nThey can\'t now. To exist, they need to be observed.\nHe looks at her. Her demeanor.\nBEN\nAre you one of them?\nWAIF\nOf course not. Not yet. But I need you to do something for me.\nShe has taken the revolver and is working at the chamber with a chisel on the desktop.\nBEN\nI want this to end.\nWAIF\nSame here Ben.\nShe hammers the chisel again and again. Something breaks off the chamber of the gun. She holds it up.\nWAIF\nThe radioactive chamber. Without it, it will fire every time.\nShe presses the gun into his hand.\nWAIF\nThere *is* a way of ending it. We can each get nothing-ness.\nBEN\nI shoot myself, this whole nightmare continues.\nShe lifts the gun so it faces her.\nWAIF\nBut if you shoot me... all that pain... all that suffering and weight... all your memories will go. Vanish. Like they were never here. Just one little squeeze.\nShe pulls the gun closer.\nWAIF\nYou know how to stop pain? You stop it at the source. I\'m the source of all the pain Ben.',
    '257': 'INT. CORNER OFFICE (FLASHBACK) - DAY #257#\nWaif fires the gun, shattering the glass behind the man. She backs away to the edge of the window.\nWAIF (O.S.)\nI didn\'t shoot myself. I jumped.\nShe looks at The Man... then lets herself fall out of the window.',
    '258': 'INT. CORNER OFFICE (FLASHBACK) - DAY #258#\nBen is beginning to understand what\'s she saying.\nWAIF\nI jumped, Ben.',
    '259': 'EXT. SKYSCRAPER (FLASHBACK)- DAY #259#\nWaif\'s body tumbles through the sky. The friction from the air pressure rips off her coat revealing jeans and shirt and sneakers underneath.',
    '260': 'INT. LODGE ROOM - CABIN - NIGHT #260#\nBEN\nYou\'re fucking lying. You\'re a liar.',
    '261': 'EXT. SKYSCRAPER (FLASHBACK) - DAY #261#\nFalling through the air toward the road below.',
    '262': 'INT. LODGE ROOM - CABIN - NIGHT #262#\nWAIF\nDo you remember the shoes Ben?',
    '263': 'EXT. AVENUE (FLASHBACK) - DAY #263#\nHer body smashing into the roof of the Forester-',
    '264': 'INT. LODGE ROOM - CABIN - NIGHT #264#\nWaif blocks Ben in. He\'s in the corner.\nWAIF\nYou remember what they looked like?\nLook at mine.',
    '265': 'INT. FORESTER (FLASHBACK) - DAY #265#\nThe aftermath of the implosion. The sneakers with red stripes.',
    '266': 'INT. LODGE ROOM - CABIN - NIGHT #266#\nWAIF\nLook at mine, Ben.\nSlowly... he cranes his head and looks at her feet.\nThe same red-striped sneakers.\nWAIF\nIt was me. I was the jumper. They couldn\'t exist in a world where we were together.\nBen shoves her against the wall. He presses the revolver against her forehead.\nWAIF\nDo it Ben ... fucking do it.\nBoth her hands run up to his wrist and hold it in place.\nWAIF\nI killed them.\nHe begins to squeeze the trigger. The hammer draws back. He pushes hard against her head and a PICTURE of his family falls from the wall.\nHe looks down at it.\nBEN\nLike they were never here?\nWAIF\nLike they were never here. I promise.\nHe puts the gun in his mouth and fires.\nWAIF\n*Noooooo!*\nHis body drops... as the Others squeeze and press inside.\nWAIF\nNo no no no...\nShe runs to the-',
    '267': 'INT. KITCHENETTE - CABIN - NIGHT #267#\nWaif finds OTHERS breaking through the door... she moves to the window and a FACE presses against it.\nWaif recoils... backs away into',
    '268': 'INT. LODGE ROOM - CABIN - NIGHT #268#',
    '269': 'INT. JACK\'S ROOM - CABIN - NIGHT #269#\nPushing inside and trying to close the door as... a shadow moves under the bed... arms GRABBING HER.\nShe falls to the ground and the door opens and Others spill in grabbing and clawing at her...\nShe SCREAMS.\nThey pull her apart - joints splitting, sinews tearing... hands and fingers *merging* into her flesh.',
    '270': 'EXT. FRONT YARD - CABIN - NIGHT #270#\nStillness.\nThe Others emerge.\nSome go into the woods, some to the path, all moving outwards like the cabin is the hub and they are the spokes.\nFinally... Waif exits. Her gait is strange, like them. Her skin is pale and lifeless. Her eyes are dead.\nShe clicks her fingers and the pale fire goes out, leaving the scene in darkness.\nCUT TO:',
    '271': 'INT. MASTER BEDROOM - HOUSE - NIGHT #271#\nBen - eyes snapping open.\nHe looks around. Four walls and a door and window with blinds. Marie sleeps beside him.',
    '272': 'INT. CORRIDOR - HOUSE - NIGHT #272#\nBen sweeps the house. Checks locks on windows and doors.\nBack at his door. He eases down the bat... observes flashlight* *flickering under the doorway opposite.\nINT. JACK\'S BEDROOM - HOUSE - NIGHT\nThe door is eased open by Ben. Jack is framed in front of us, holding a flashlight.\nBEN\nHey buddy. Whatcha doing?\nJACK\nI wanna show you something.\nHe sits in front of Jack. They look at the mirror together.',
    '273': 'INT. JACK\'S BEDROOM - HOUSE - DAY #273#\nMorning light on Ben. He stirs to find he is sleeping beside Jack. Marie is watching from the doorway.\nMARIE\nTrouble sleeping again?\nBEN\nThe pair of us.\nMarie exits into the corridor.\nMARIE (O.S.)\nGet up and I\'ll make you coffee.',
    '274': 'INT. KITCHEN - HOUSE - DAY #274#\nMarie draws a set of equations on a transparent easel in the kitchen. Ben trudges in, buttoning shirt.\nHis right foot steps on the tail fin of a toy car.\nBEN\nDamn it.\nHe buckles and pulls it out of his foot. A drop of blood.\nMARIE\nI\'ll get you a bandage.',
    '275': 'EXT. SUBURBAN HOME - DAY #275#\nWe drift up the driveway as the family emerge. Ben\'s F-250 parked beside the undamaged Forester. Ben buckles Jack into the Forester\'s child seat.\nBEN\nI need you to promise to do exactly what I say.\nJACK\nI promise.',
    '276': 'EXT. UPSTATE NEW YORK - DAY #276#\nThe Forester drives southward. Joining the interstate. We\'ve seen all this before.',
    '277': 'INT. FORESTER (MOVING) - DAY #277#\nIn the rear is Jack, staring at his reflection in the window. Moving his finger between the glass and his face.\nJACK\nSee?\nBen looks back at him.\nJACK\nI told you I was coming with you.\nBen smiles at him in the rearview, turns eyes back to the road. He is approaching a BUSY INTERSECTION...\nThe lights turn RED.\nBEN\nShit, I hate this-\nMARIE\nLanguage.\nBEN\nSorry. We\'ll be stuck here five minutes.\nHe puts the car in park. Marie look up at the sky.\nMARIE\nLooks like it\'ll be a nice day.\nIn the back, Jack traces his finger against the glass.',
    '278': 'EXT. INTERSECTION - DAY #278#\nOn Jack... his finger running patterns in the glass.\nDrifting up and away from Jack, moving across the roof of the car... pulling back wider and wider until the busy crossroads fills the screen.\n> FADE TO BLACK.\n>_THE END_<',
}


SCENE_LOOKUP = {
    "1": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "2": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "3": {"location": "INT. JACK'S BEDROOM - HOUSE - NIGHT"},
    "4": {"location": "INT. JACK'S BEDROOM - HOUSE - DAY"},
    "5": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "6": {"location": 'EXT. SUBURBAN HOME - DAY'},
    "7": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "8": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "9": {"location": 'EXT. NEW YORK - DAY'},
    "10": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "11": {"location": 'INT. MOTEL ROOM - DAY'},
    "12": {"location": 'INT. BATHROOM - MOTEL ROOM - DAY'},
    "13": {"location": 'INT. MOTEL ROOM - DAY'},
    "14": {"location": 'EXT. MOTEL - DAY'},
    "15": {"location": 'INT. PICKUP (MOVING) - NEW YORK STATE - DAY'},
    "16": {"location": 'EXT. MUNICIPAL COURT BUILDING - DAY'},
    "17": {"location": 'INT. CONFERENCE ROOM - COURT BUILDING - DAY'},
    "18": {"location": 'INT. CAFETERIA - COURT BUILDING - DAY'},
    "19": {"location": 'INT. CORRIDOR - COURT BUILDING - DAY'},
    "20": {"location": 'EXT. PARKING LOT - COURT BUILDING - DAY'},
    "21": {"location": 'INT. PICKUP (MOVING) - COURT BUILDING - DAY'},
    "22": {"location": 'EXT. DARK WATER - DAY'},
    "23": {"location": 'EXT. BRIDGE - UPSTATE NEW YORK - DAY'},
    "24": {"location": 'INT. PICKUP (MOVING) - BRIDGE - DAY'},
    "25": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "26": {"location": 'EXT. WORN ROAD - DAY'},
    "27": {"location": 'EXT. BROKEN BOW - DAY'},
    "28": {"location": 'INT. PICKUP (MOVING) - BROKEN BOW - DAY'},
    "29": {"location": 'EXT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "30": {"location": 'EXT. ACCESS ROAD - DAY'},
    "31": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "32": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "33": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "34": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "35": {"location": 'INT. BATHROOM - CABIN - DAY'},
    "36": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "37": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "38": {"location": 'EXT. REAR - CABIN - DAY'},
    "39": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "40": {"location": "INT. JACK'S ROOM - CABIN - DAY"},
    "41": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "42": {"location": 'INT. LIVING ROOM - HOUSE - DAY'},
    "43": {"location": 'INT. PICKUP (MOVING) - WOODS - DAY'},
    "44": {"location": 'EXT. MAIN STREET - BROKEN BOW - DAY'},
    "45": {"location": 'EXT. LAST CHANCE SUPPLY - DAY'},
    "46": {"location": 'INT. LAST CHANCE SUPPLY - DAY'},
    "47": {"location": 'INT. PICKUP (MOVING) - DAY'},
    "48": {"location": 'EXT. SCENIC STOP - DAY'},
    "49": {"location": 'INT. FOREST CLEARING - DAY'},
    "50": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "51": {"location": 'INT. LODGE ROOM - CABIN - SUNSET'},
    "52": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "53": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "54": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "55": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "56": {"location": 'INT. PICKUP (MOVING) - FRONT YARD - DAY'},
    "57": {"location": 'INT. ATTIC - CABIN - DAY'},
    "58": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "59": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "60": {"location": "INT. JACK'S ROOM - CABIN - DAY"},
    "61": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "62": {"location": 'INT. LAST CHANCE SUPPLY - DAY'},
    "63": {"location": 'EXT. LAST CHANCE SUPPLY - DAY'},
    "64": {"location": 'INT. CHEVROLET - DAY'},
    "65": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "66": {"location": 'EXT. FOREST TURN - DAY'},
    "67": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "68": {"location": 'EXT. FOREST PATH - DAY'},
    "69": {"location": 'INT. PICKUP - DAY'},
    "70": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "71": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "72": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "73": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "74": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "75": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "76": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "77": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "78": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "80": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "82": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "84": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "85": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "87": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "89": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "91": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "92": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "93": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "94": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "95": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "96": {"location": 'INT. BATHROOM - CABIN - NIGHT'},
    "97": {"location": 'INT. LODGE ROOM - CABIN - CONTINUOUS'},
    "98": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "99": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "100": {"location": 'INT. BATHROOM - CABIN - NIGHT'},
    "101": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "102": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "103": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "104": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "105": {"location": 'INT. LODGE ROOM - CABIN - CONTINUOUS'},
    "106": {"location": 'EXT. FRONT PORCH - CABIN - NIGHT'},
    "107": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "108": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "109": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "110": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "111": {"location": 'INT. PICKUP - NIGHT'},
    "112": {"location": 'EXT. PICKUP - NIGHT'},
    "113": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "114": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "115": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "116": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "117": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "118": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "119": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "120": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "121": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "122": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "123": {"location": 'INT. KITCHENETTE - CABIN - DAY'},
    "124": {"location": 'EXT. PICKUP - DAY'},
    "125": {"location": 'INT. PICKUP (MOVING) - BROKEN BOW - DAY'},
    "126": {"location": 'EXT. MAIN STREET - DAY'},
    "127": {"location": 'INT. SECOND LAST CHANCE SUPPLY - DAY'},
    "128": {"location": 'INT. BACKROOM - SECOND LAST CHANCE SUPPLY - DAY'},
    "129": {"location": 'INT. SECOND LAST CHANCE SUPPLY - DAY'},
    "130": {"location": 'EXT. SECOND LAST CHANCE SUPPLY - DAY'},
    "131": {"location": 'EXT. CLEARING - NEAR CABIN - DAY'},
    "132": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "133": {"location": 'INT. INSTITUTION - DAY'},
    "134": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "135": {"location": 'INT. BEDROOM - DAY'},
    "136": {"location": 'EXT. DRIVEWAY - DAY'},
    "137": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "138": {"location": 'INT. HALLWAY - DAY'},
    "139": {"location": 'INT. BEDROOM - ON WAIF - DAY'},
    "140": {"location": 'EXT. NEW YORK STREET - DAY'},
    "141": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "142": {"location": 'INT. OFFICE RECEPTION - DAY'},
    "143": {"location": 'INT. CORNER OFFICE - NEW YORK - DAY'},
    "144": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "145": {"location": 'INT. INTENSIVE CARE BED - HOSPITAL - DAY'},
    "146": {"location": 'INT. INTENSIVE CARE BED - NIGHT'},
    "147": {"location": 'INT. STORAGE ROOM - HOSPITAL - NIGHT'},
    "148": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "149": {"location": 'INT. CORRIDOR - INSTITUTION - DAY'},
    "150": {"location": 'INT. PATIENT ROOM - NIGHT'},
    "151": {"location": 'EXT. INSTITUTION - NIGHT'},
    "152": {"location": 'INT. DRIVER COCKPIT - NIGHT'},
    "153": {"location": 'EXT. FREEWAY - NIGHT'},
    "154": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "155": {"location": 'EXT. FREEWAY - NIGHT'},
    "156": {"location": 'INT. PADDED ROOM - DAY'},
    "157": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "158": {"location": 'INT. PADDED ROOM - DAY'},
    "159": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "160": {"location": 'INT. PICKUP (MOVING) - FOREST ROAD - DAY'},
    "161": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "162": {"location": 'EXT. NORTH HAVEN - DAY'},
    "163": {"location": 'INT. PICKUP (MOVING) - NORTH HAVEN - DAY'},
    "164": {"location": 'EXT. MANSION - DAY'},
    "165": {"location": 'INT. KITCHENETTE - MANSION - DAY'},
    "166": {"location": "INT. PRESTON'S LAB - DAY"},
    "167": {"location": 'EXT. BRIDGE - DAY'},
    "168": {"location": 'EXT. SUBURB - DAY'},
    "169": {"location": 'INT. PICKUP (MOVING) - SUBURB - DAY'},
    "170": {"location": 'EXT. SUBURB - DAY'},
    "171": {"location": 'INT. PICKUP - DAY'},
    "172": {"location": 'EXT. DRIVEWAY - HOUSE - DAY'},
    "173": {"location": 'INT. PICKUP - DAY'},
    "174": {"location": 'EXT. HOUSE - DAY'},
    "175": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "176": {"location": 'EXT. BACKYARD - HOUSE - DAY'},
    "177": {"location": "EXT. NEIGHBOR'S GARDEN - DAY"},
    "178": {"location": "EXT. SIDE OF NEIGHBOR'S HOUSE - CONTINUOUS"},
    "179": {"location": 'EXT. SUBURB - CONTINUOUS'},
    "180": {"location": 'EXT. SCENIC PULLOUT - DAY'},
    "181": {"location": 'INT. PICKUP - DAY'},
    "182": {"location": 'INT. PICKUP (MOVING) - FRONT YARD - DAY'},
    "183": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "184": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "185": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "186": {"location": 'EXT. CABIN - DAY'},
    "187": {"location": 'EXT. FOREST - DAY'},
    "188": {"location": 'EXT. LAKESIDE - DAY'},
    "189": {"location": 'EXT. FOREST TRAIL - DAY'},
    "190": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "191": {"location": 'EXT. FRONT YARD - CABIN - DAY'},
    "192": {"location": 'INT. LODGE ROOM - CABIN - DAY'},
    "193": {"location": 'INT. FORESTER (MOVING) - BROKEN BOW - DAY'},
    "194": {"location": 'INT. T.R. GENERAL SUPPLIES - DAY'},
    "195": {"location": 'INT. FORESTER (MOVING) - BROKEN BOW - DAY'},
    "196": {"location": 'EXT. HIGHWAY - DAY'},
    "197": {"location": 'EXT. SUBURB - DAY'},
    "198": {"location": 'INT. FORESTER (MOVING) - SUBURB - SUNSET'},
    "199": {"location": 'EXT. SUBURBAN HOME - SUNSET'},
    "200": {"location": 'INT. HALLWAY - HOUSE - NIGHT'},
    "201": {"location": 'INT. HOUSE - FIRST FLOOR - NIGHT'},
    "202": {"location": "INT. JACK'S ROOM - HOUSE - NIGHT"},
    "203": {"location": 'INT. KITCHEN - HOUSE - NIGHT'},
    "204": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "205": {"location": 'EXT. HOUSE - NIGHT'},
    "206": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "207": {"location": 'EXT. HOUSE - NIGHT'},
    "208": {"location": 'INT. LIVING ROOM - HOUSE - NIGHT'},
    "209": {"location": "INT. JACK'S BEDROOM - HOUSE - NIGHT"},
    "210": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "211": {"location": 'EXT. FRONT LAWN - HOUSE - NIGHT'},
    "212": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "213": {"location": "INT. JACK'S ROOM - HOUSE - NIGHT"},
    "214": {"location": 'INT. LANDING - HOUSE - NIGHT'},
    "215": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "216": {"location": 'INT. STAIRCASE/GROUND FLOOR - HOUSE - NIGHT'},
    "217": {"location": 'INT. BASEMENT - HOUSE - NIGHT'},
    "218": {"location": 'INT. HALLWAY - HOUSE - NIGHT'},
    "219": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "220": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "221": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "222": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "223": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "224": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "225": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "226": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "227": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "228": {"location": 'INT. DOWNSTAIRS - HOUSE - NIGHT'},
    "229": {"location": 'EXT. FRONT LAWN - HOUSE - NIGHT'},
    "230": {"location": 'INT. FORESTER - NIGHT'},
    "232": {"location": 'EXT. HIGHWAY - NIGHT'},
    "233": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "234": {"location": 'INT. / EXT. FORESTER - NIGHT'},
    "235": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "236": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "237": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "238": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "239": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "240": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "241": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "242": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "243": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "244": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "245": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "246": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "247": {"location": 'INT. LODGE ROOM - NIGHT'},
    "248": {"location": 'INT. PICKUP - NIGHT'},
    "249": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "250": {"location": 'INT. PICKUP - NIGHT'},
    "251": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "252": {"location": 'INT. PICKUP - NIGHT'},
    "253": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "254": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "255": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "256": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "257": {"location": 'INT. CORNER OFFICE (FLASHBACK) - DAY'},
    "258": {"location": 'INT. CORNER OFFICE (FLASHBACK) - DAY'},
    "259": {"location": 'EXT. SKYSCRAPER (FLASHBACK)- DAY'},
    "260": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "261": {"location": 'EXT. SKYSCRAPER (FLASHBACK) - DAY'},
    "262": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "263": {"location": 'EXT. AVENUE (FLASHBACK) - DAY'},
    "264": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "265": {"location": 'INT. FORESTER (FLASHBACK) - DAY'},
    "266": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "267": {"location": 'INT. KITCHENETTE - CABIN - NIGHT'},
    "268": {"location": 'INT. LODGE ROOM - CABIN - NIGHT'},
    "269": {"location": "INT. JACK'S ROOM - CABIN - NIGHT"},
    "270": {"location": 'EXT. FRONT YARD - CABIN - NIGHT'},
    "271": {"location": 'INT. MASTER BEDROOM - HOUSE - NIGHT'},
    "272": {"location": 'INT. CORRIDOR - HOUSE - NIGHT'},
    "273": {"location": "INT. JACK'S BEDROOM - HOUSE - DAY"},
    "274": {"location": 'INT. KITCHEN - HOUSE - DAY'},
    "275": {"location": 'EXT. SUBURBAN HOME - DAY'},
    "276": {"location": 'EXT. UPSTATE NEW YORK - DAY'},
    "277": {"location": 'INT. FORESTER (MOVING) - DAY'},
    "278": {"location": 'EXT. INTERSECTION - DAY'},
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(SCRIPT_DIR, "index.html")

# ── CLI ───────────────────────────────────────────────────────────────────
def parse_args():
    global PORT, CSV_PATH, FRAMES_DIR, REFS_DIR
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i + 1 < len(args):
            i += 1; PORT = int(args[i])
        elif args[i] == "--csv" and i + 1 < len(args):
            i += 1; CSV_PATH = args[i]
        elif args[i] == "--frames-dir" and i + 1 < len(args):
            i += 1; FRAMES_DIR = args[i]
        elif args[i] == "--refs-dir" and i + 1 < len(args):
            i += 1; REFS_DIR = args[i]
        i += 1

# ── CSV ───────────────────────────────────────────────────────────────────
CSV_COLUMNS = [
    "scene_number", "shot_number", "generation_number", "verbatim_instructions",
    "lens", "aspect_ratio", "quality", "curated_description",
    "fountain_description", "fountain_text", "iteration_history",
    "characters", "location", "generation_method", "iteration_count",
    "source_frame", "estimated_cost", "prompt", "output_file", "status",
    "endpoint", "version_history"
]

def read_csv():
    if not os.path.exists(CSV_PATH):
        return [], CSV_COLUMNS
    with open(CSV_PATH, "r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames) if reader.fieldnames else CSV_COLUMNS
    # Auto-migrate: add any columns from CSV_COLUMNS not yet in the file
    missing = [c for c in CSV_COLUMNS if c not in fieldnames]
    if missing:
        for c in missing:
            fieldnames.append(c)
            for r in rows:
                r[c] = ""
        # Write migrated version back
        tmp = CSV_PATH + ".tmp"
        with open(tmp, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, CSV_PATH)
    return rows, fieldnames

def write_csv(rows, fieldnames):
    # Auto-backup before every write
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(CSV_PATH)), ".csv_backups")
    os.makedirs(backup_dir, exist_ok=True)
    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"storyboard_shots.{ts}.csv")
    with open(backup_path, "w", newline="") as bf:
        bw = csv.DictWriter(bf, fieldnames=fieldnames)
        bw.writeheader()
        bw.writerows(rows)
    # Prune backups older than 7 days, keep max 50
    backups = sorted(os.listdir(backup_dir))
    if len(backups) > 50:
        for old in backups[:-50]:
            os.remove(os.path.join(backup_dir, old))
    # Write main file
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, CSV_PATH)

def update_shot_field(row_index, field, value):
    rows, fieldnames = read_csv()
    if row_index < 0 or row_index >= len(rows):
        return False
    rows[row_index][field] = value
    write_csv(rows, fieldnames)
    return True

# ── Images ────────────────────────────────────────────────────────────────
def list_images(directory):
    images = []
    base = Path(directory)
    if not base.exists():
        return images
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS:
            images.append(str(p.relative_to(base)))
    return images

def find_in_tree(base_dir, filename):
    """Search recursively for a file by basename in base_dir."""
    name_lower = filename.lower()
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and p.name.lower() == name_lower:
            return str(p)
    # Fallback: substring match
    for p in Path(base_dir).rglob("*"):
        if p.is_file() and name_lower in p.name.lower():
            return str(p)
    return None

# ── Content types ─────────────────────────────────────────────────────────
CT = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "gif": "image/gif", "html": "text/html; charset=utf-8",
    "json": "application/json"
}

# ── Handler ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _respond(self, status, content_type, body_bytes):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(body_bytes))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body_bytes)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-transfer

    def _json(self, data, status=200):
        self._respond(status, "application/json", json.dumps(data).encode())

    def _file(self, path, content_type, status=200):
        if not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            self._respond(status, content_type, f.read())

    def _error(self, msg, status=400):
        self._json({"error": msg}, status)

    def _safe_path(self, base_dir, rel_path):
        """Resolve rel_path to a file under base_dir, preventing traversal."""
        # Strip any leading slashes and normalize
        clean = os.path.normpath(rel_path).lstrip("/")
        full = os.path.join(base_dir, clean)
        # Must be within base_dir
        if not full.startswith(os.path.abspath(base_dir)):
            return None
        return full if os.path.isfile(full) else None

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path

        if path == "/":
            self._file(HTML_PATH, CT["html"])

        elif path == "/api/shots":
            rows, _ = read_csv()
            self._json({"shots": rows})

        elif path == "/api/frames":
            images = list_images(FRAMES_DIR)
            frames = {}
            frames_lower = {}
            for img in images:
                base = os.path.basename(img)
                frames[base] = img
                frames_lower[base.lower()] = img
            self._json({"frames": frames, "frames_lower": frames_lower, "all": images})

        elif path == "/api/refs":
            self._json({"images": list_images(REFS_DIR)})

        elif path.startswith("/api/frame/"):
            filename = urllib.parse.unquote(path[len("/api/frame/"):])
            filepath = find_in_tree(FRAMES_DIR, filename)
            if filepath:
                ext = os.path.splitext(filepath)[1].lower().lstrip(".")
                self._file(filepath, CT.get(ext, "application/octet-stream"))
            else:
                self.send_error(404, "Frame not found")

        elif path.startswith("/api/ref/"):
            rel = urllib.parse.unquote(path[len("/api/ref/"):])
            filepath = self._safe_path(REFS_DIR, rel)
            if not filepath:
                filepath = find_in_tree(REFS_DIR, rel)
            if filepath:
                ext = os.path.splitext(filepath)[1].lower().lstrip(".")
                self._file(filepath, CT.get(ext, "application/octet-stream"))
            else:
                self.send_error(404, "Reference not found")

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/api/reorder":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            scene = data.get("scene_number", "")
            order = data.get("order", [])  # list of output_file names in new order
            rows, fieldnames = read_csv()
            # Update shot_number for all shots in this scene
            for i, fn in enumerate(order):
                for r in rows:
                    if r.get('output_file', '').strip() == fn:
                        r['shot_number'] = str(i + 1)
                        break
            write_csv(rows, fieldnames)
            self._json({"ok": True})

        elif self.path == "/api/generate":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            row_index = data.get("row_index")
            if row_index is None:
                return self._error("Missing row_index")
            rows, fieldnames = read_csv()
            if row_index < 0 or row_index >= len(rows):
                return self._error("Row index out of range", 400)
            shot = rows[row_index]
            # Build prompt: use existing if regenerating, else build cinematic prompt
            if shot.get("prompt"):
                prompt = shot["prompt"]
            else:
                base = (shot.get("curated_description") or shot.get("verbatim_instructions") or "").strip()
                if not base:
                    return self._error("Shot has no description to generate from", 400)
                # Build full cinematic prompt like the storyboarding pipeline
                lens = shot.get("lens", "").strip()
                loc = shot.get("location", "").strip()
                ratio = shot.get("aspect_ratio", "2.39:1").strip()
                parts = [base]
                if lens:
                    parts.append(f"Shot with {lens} lens")
                if loc:
                    parts.append(f"{loc}")
                parts.append("Desaturated palette, cool shadows")
                parts.append("Photorealistic cinematic still from an indie horror film")
                parts.append("Scope {}. No text, no watermark, no logos.".format(ratio))
                prompt = ". ".join(parts)
            # Load API key
            key = None
            env_path = os.path.expanduser("/opt/data/profiles/heavy/.env")
            if os.path.exists(env_path):
                with open(env_path) as ef:
                    for line in ef:
                        if "OPENAI_KEY" in line or "VOICE_TOOLS_OPENAI_KEY" in line:
                            key = line.strip().split("=", 1)[1].strip().strip("'").strip('"')
                            break
            if not key:
                return self._error("OpenAI API key not found", 500)
            # Call GPT Image 2
            import urllib.request, base64
            body = json.dumps({
                "model": "gpt-image-2",
                "prompt": prompt,
                "n": 1,
                "size": "2560x1072",
                "quality": "medium"
            }).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/images/generations",
                data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            )
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()[:500]
                return self._error(f"GPT Image 2 blocked: {err_body}", 400)
            except Exception as e:
                return self._error(f"API error: {str(e)}", 500)
            d = resp.get("data", [{}])
            if not d:
                return self._error("No image data in response", 500)
            if "b64_json" in d[0]:
                img_bytes = base64.b64decode(d[0]["b64_json"])
            else:
                img_bytes = urllib.request.urlopen(d[0]["url"]).read()
            # Versioning: push current file to history, increment version
            import re
            old_file = shot.get("output_file", "").strip()
            old_sc = shot.get("scene_number", "XX")
            if old_file:
                # Push current to version history
                try:
                    history = json.loads(shot.get("version_history") or "[]")
                except json.JSONDecodeError:
                    history = []
                history.append(old_file)
                shot["version_history"] = json.dumps(history)
                # Determine new version number
                v_match = re.search(r'_v(\d+)', old_file)
                if v_match:
                    new_v = int(v_match.group(1)) + 1
                    base_name = old_file[:v_match.start()]
                    ext = os.path.splitext(old_file)[1]
                else:
                    new_v = 2
                    base_name = os.path.splitext(old_file)[0]
                    ext = os.path.splitext(old_file)[1]
            else:
                new_v = 1
                base_name = f"waif_sc_{old_sc}"
                ext = ".png"
            output_file = f"{base_name}_v{new_v}{ext}"
            out_path = os.path.join(FRAMES_DIR, output_file)
            with open(out_path, "wb") as of:
                of.write(img_bytes)
            # Update CSV with all fields matching earlier pipeline
            shot["status"] = "generated"
            shot["output_file"] = output_file
            shot["prompt"] = prompt
            shot["aspect_ratio"] = shot.get("aspect_ratio") or "2.39:1"
            shot["quality"] = shot.get("quality") or "medium"
            shot["generation_method"] = shot.get("generation_method") or "generation"
            shot["estimated_cost"] = shot.get("estimated_cost") or "$0.04"
            if not shot.get("lens"):
                shot["lens"] = "28mm"
            if not shot.get("endpoint"):
                shot["endpoint"] = "/v1/images/generations (JSON POST)"
            if not shot.get("curated_description"):
                shot["curated_description"] = (shot.get("verbatim_instructions") or "")[:300]
            # Auto-fill fountain text
            sc = shot.get("scene_number", "")
            if sc in SCENE_TEXT and not shot.get("fountain_text"):
                shot["fountain_text"] = SCENE_TEXT[sc]
            # Auto-detect characters from instructions
            instr = (shot.get("verbatim_instructions") or "").lower()
            if not shot.get("characters") and instr:
                chars = []
                has_jrm = any(k in instr for k in ("jrm", "jonathan"))
                for kw, name in [("ben", "Ben"), ("jrm", "Ben (JRM)"), ("jonathan", "Ben (JRM)"), ("marie", "Marie"), ("waif", "Waif"), ("jack", "Jack")]:
                    if kw in instr and not (kw == "ben" and has_jrm):
                        if name not in chars:
                            chars.append(name)
                if chars:
                    shot["characters"] = ", ".join(chars)
            write_csv(rows, fieldnames)
            self._json({"ok": True, "output_file": output_file, "size_kb": len(img_bytes) // 1024, "version": new_v, "history": json.loads(shot.get("version_history", "[]"))})

        elif self.path == "/api/swap_version":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            row_index = data.get("row_index")
            swap_file = data.get("swap_file", "").strip()
            if row_index is None or not swap_file:
                return self._error("Missing row_index or swap_file")
            rows, fieldnames = read_csv()
            if row_index < 0 or row_index >= len(rows):
                return self._error("Row index out of range", 400)
            shot = rows[row_index]
            try:
                history = json.loads(shot.get("version_history") or "[]")
            except json.JSONDecodeError:
                history = []
            if swap_file not in history:
                return self._error("File not in version history", 400)
            # Swap: current goes into history, swap_file becomes current
            current = shot["output_file"]
            history.remove(swap_file)
            history.append(current)
            shot["output_file"] = swap_file
            shot["version_history"] = json.dumps(history)
            write_csv(rows, fieldnames)
            self._json({"ok": True, "current": swap_file, "history": history})

        elif self.path == "/api/create":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            rows, fieldnames = read_csv()
            new_row = {f: data.get(f, "") for f in fieldnames}
            if not new_row.get("status"):
                new_row["status"] = "pending"
            sc = new_row.get("scene_number", "")
            existing = [int(r.get("shot_number") or 0) for r in rows if r.get("scene_number") == sc]
            new_row["shot_number"] = str(max(existing) + 1 if existing else 1)
            # Auto-fill fountain text, location, and characters
            instr = (new_row.get("verbatim_instructions") or "").lower()
            if sc in SCENE_TEXT and not new_row.get("fountain_text"):
                new_row["fountain_text"] = SCENE_TEXT[sc]
            # Location from scene lookup
            if sc in SCENE_LOOKUP:
                if not new_row.get("location"):
                    new_row["location"] = SCENE_LOOKUP[sc]["location"]
            elif not new_row.get("location"):
                # Infer from instructions keywords
                for kw, loc in [("cabin", "Cabin — Broken Bow"), ("court", "Municipal Courthouse"), ("motel", "Motel"), ("pickup", "Pickup Truck"), ("suburban", "Suburban House"), ("intersection", "The Intersection"), ("broken bow", "Broken Bow")]:
                    if kw in instr:
                        new_row["location"] = loc
                        break
            # Characters: keyword matching from instructions + scene lookup fallback
            if not new_row.get("characters"):
                chars = []
                for kw, name in [("ben", "Ben"), ("jrm", "Ben (JRM)"), ("jonathan", "Ben (JRM)"), ("marie", "Marie"), ("waif", "Waif"), ("jack", "Jack"), ("neighbor", "Neighbor"), ("mother", "The Mother"), ("lawyer", "Lawyer"), ("jamie", "Jamie"), ("ricky", "Ricky"), ("schrödinger", "Schrödinger"), ("schrodinger", "Schrödinger")]:
                    if kw in instr:
                        chars.append(name)
                if chars:
                    new_row["characters"] = ", ".join(chars)
                elif sc in SCENE_LOOKUP and SCENE_LOOKUP[sc].get("characters"):
                    new_row["characters"] = SCENE_LOOKUP[sc]["characters"]
            rows.append(new_row)
            write_csv(rows, fieldnames)
            self._json({"ok": True, "row": new_row})

        elif self.path == "/api/update":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                return self._error("Invalid JSON")
            idx = data.get("row_index")
            field = data.get("field", "status")
            value = data.get("value", "")
            if idx is None:
                return self._error("Missing row_index")
            if update_shot_field(idx, field, value):
                self._json({"ok": True})
            else:
                self._error("Row index out of range", 400)
        else:
            self.send_error(404)

# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parse_args()
    print(f"🎬 Shot Dash")
    print(f"   CSV:      {CSV_PATH}")
    print(f"   Frames:   {FRAMES_DIR}")
    print(f"   Refs:     {REFS_DIR}")
    print(f"   → http://localhost:{PORT}")
    print(f"   Ctrl+C to stop\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()

if __name__ == "__main__":
    main()
