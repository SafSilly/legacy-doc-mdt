"""Cadet practice material derived from the July 2026 DOC SOP handbook."""

SOP_SOURCE_URL = "https://docs.google.com/document/d/1apr4I8HcWyiw_kv9HjPVJHJOS51zyPFGPN0JrMkoIA0/edit?usp=sharing"


def mc(prompt, options, answer, guidance):
    return {"type": "multiple_choice", "prompt": prompt, "options": options, "answer": answer, "guidance": guidance}


def written(prompt, guidance):
    return {"type": "written", "prompt": prompt, "guidance": guidance}


CADET_TRAINING_CATEGORIES = [
    {
        "slug": "ten-codes",
        "title": "10-Codes",
        "summary": "Radio status, location, backup, transport, and emergency traffic.",
        "questions": [
            mc("What does 10-4 mean?", ["Repeat", "Acknowledgement", "Negative", "Arrived on scene"], "Acknowledgement", "10-4 means acknowledgement."),
            mc("Which code reports your location?", ["10-19", "10-20", "10-23", "10-79"], "10-20", "10-20 means location."),
            mc("Which code requests non-emergency backup?", ["10-52", "10-77", "10-78", "10-99"], "10-77", "10-77 is a non-emergency backup request; 10-78 is emergency backup."),
            mc("What does 10-23 mean?", ["Enroute", "Arrived on scene", "In service", "Report in person"], "Arrived on scene", "10-23 means arrived on scene."),
            mc("Which code identifies a transport unit?", ["10-76", "10-79", "10-91", "10-19"], "10-91", "10-91 identifies a transport unit."),
            mc("An officer in distress should transmit which code?", ["10-14", "10-71", "10-78", "10-99"], "10-99", "10-99 means officer in distress."),
            written("Write a concise radio transmission stating that unit 100 is at the yard and needs non-emergency backup for two aggressive, unarmed inmates.", "Include the unit identifier, 10-77, the yard location, the number of inmates, and that they are aggressive and unarmed."),
            written("Explain the difference between 10-7, 10-8, 10-41, and 10-42.", "10-7 is out of service, 10-8 is in service, 10-41 is on duty, and 10-42 is off duty."),
            written("You are enroute to the prison with an estimated arrival of five minutes. Write the essential radio traffic.", "Use 10-19 or 10-76 to communicate enroute status and 10-79 to give the five-minute ETA."),
            written("Describe when you would use 10-77 instead of 10-78.", "Use 10-77 when backup is needed but the situation is not an emergency; use 10-78 for an urgent emergency backup request."),
        ],
    },
    {
        "slug": "use-of-force",
        "title": "Use of Force",
        "summary": "Force progression, proportional response, less-lethal tools, and lethal-force limits.",
        "questions": [
            mc("What is the first level of the use-of-force progression?", ["Verbal commands", "Officer presence", "Empty-hand control", "Less-lethal methods"], "Officer presence", "Professional, nonthreatening officer presence can deter and diffuse a situation."),
            mc("Which is a soft empty-hand technique?", ["Punch", "Kick", "Joint lock", "Nightstick strike"], "Joint lock", "Grabs, holds, joint locks, and tackling are soft empty-hand control techniques."),
            mc("Which item is classified as less-lethal in the SOP?", ["Service pistol", "Beanbag deployment", "Rifle", "Warning shot"], "Beanbag deployment", "Nightsticks, tasers, and beanbag deployments are listed as less-lethal methods."),
            mc("When may lethal force be used?", ["Whenever an inmate refuses an order", "To stop property damage", "Only against an immediate threat to life", "After any warning"], "Only against an immediate threat to life", "The person must present an immediate threat to the life of an officer or innocent third party."),
            mc("What must follow any use of lethal force?", ["A visitation log", "An incident report", "A transport request", "A promotion review"], "An incident report", "Every use of lethal force requires an incident report."),
            mc("After calm commands fail, what may an officer do before applying force?", ["Leave without reporting", "Issue a clear warning", "Immediately fire", "Skip directly to hard control"], "Issue a clear warning", "The SOP allows increased volume, shorter commands, and a warning such as advising that a taser will be used."),
            written("An inmate refuses to stop moving but is not attacking anyone. Describe a proportional progression of force.", "Begin with professional presence and calm commands, use clearer/shorter commands and a warning, then reasonable empty-hand or less-lethal control only as resistance requires."),
            written("Explain why punches and kicks are not an appropriate first response to passive resistance.", "They are hard empty-hand defence techniques. The officer should begin lower in the progression and match force to the threat and resistance."),
            written("List the conditions and follow-up required if a firearm is used.", "There must be an immediate threat to life, a warning shot should be issued where possible, and an incident report must be written afterward."),
            written("Give an example of a calm verbal command followed by a lawful warning.", "A suitable response uses a short, clear command, then clearly states the reasonable consequence if the person continues to refuse."),
        ],
    },
    {
        "slug": "risk-and-escalation",
        "title": "Ask, Tell, Make & Risk Levels",
        "summary": "Order escalation and the four prison operational risk levels.",
        "questions": [
            mc("What is the correct Ask, Tell, Make order?", ["Warn, ask, restrain", "Ask, order, warn/make", "Tell, ask, make", "Ask, make, tell"], "Ask, order, warn/make", "Ask politely, tell by issuing an order, then advise and apply a reasonable means of making the person comply."),
            mc("At Risk Level One, weapons should generally be...", ["Drawn", "In the armory", "Holstered", "Pointed low"], "Holstered", "Level One is regular operation and mandates that weapons remain holstered."),
            mc("Which risk level permits tasers and nightsticks to be drawn for protection?", ["Level One", "Level Two", "Level Three", "Level Four"], "Level Two", "Level Two permits stun guns and nightsticks to be drawn."),
            mc("Which risk level authorizes beanbag shotgun engagement?", ["Level One", "Level Two", "Level Three", "Level Four only"], "Level Three", "Level Three authorizes beanbag shotgun engagement and advises tactical ERT uniform."),
            mc("Who may authorize lethal weapons at Risk Level Four?", ["Any cadet", "Any inmate supervisor", "DOC Command and ERT Supervisors", "Only PD"], "DOC Command and ERT Supervisors", "DOC Command and ERT Supervisors may authorize lethal weapons at Level Four."),
            mc("What documentation is required after escalating the prison risk level?", ["Vehicle log", "Incident report", "Visitation log", "Quiz request"], "Incident report", "Any escalation of the risk level requires an incident report to be written and published."),
            written("Write an Ask, Tell, Make sequence for ordering an inmate to face the wall for a search.", "Include a polite request, a direct order, then a clear warning of reasonable force before applying it."),
            written("Four hostile inmates are confronting one officer. Explain the risk-level consideration.", "Being outnumbered at least four-to-one with hostility or aggression is a Level Two trigger, allowing tasers and nightsticks to be drawn."),
            written("A severe riot and escape attempt begins. Describe the Level Four response.", "Command/ERT supervisors may authorize lethal weapons, full ERT riot gear is advised, and inmates should be locked in cells until the level falls."),
            written("No Command or ERT supervisors are present. How may on-duty officers temporarily change the risk level?", "On-duty officers control the temporary level; without Command or ERT supervisors, there must be unanimous agreement."),
        ],
    },
    {
        "slug": "vehicles-and-transport",
        "title": "Vehicles & Transport",
        "summary": "DOC vehicle limits, transport preparation, communications, and armed response.",
        "questions": [
            mc("When may DOC lights and sirens be used?", ["Any emergency trip", "To report for duty", "Only for a prison transport", "During city patrol"], "Only for a prison transport", "The SOP permits lights and sirens only for a prison transport."),
            mc("What body colors are required when setting up a DOC vehicle?", ["White or blue", "Steel Grey or Black", "Any department color", "Black only"], "Steel Grey or Black", "The vehicle must have DOC livery and a Steel Grey or Black body color."),
            mc("Before inmates enter the transport vehicle, officers should...", ["Remove cuffs", "Soft cuff them, hard cuffing only for non-cooperation", "Always hard cuff them", "Seat them before cuffing"], "Soft cuff them, hard cuffing only for non-cooperation", "All inmates are soft cuffed first; hard cuffs are used when they do not cooperate."),
            mc("What should happen once everyone is seated?", ["Unlock the vehicle", "Lock the vehicle", "Start lights and sirens", "Call Code 4"], "Lock the vehicle", "The transport vehicle is locked after everyone is seated."),
            mc("If fired upon during transport, officers may...", ["Pursue attackers", "Stop and search", "Return fire in defence but not pursue", "Release inmates"], "Return fire in defence but not pursue", "Officers may defend themselves, must not pursue, and should not stop the transport."),
            mc("If the transport vehicle is disabled, who must be notified immediately?", ["Visitors", "PD", "The grocery store", "Only inmates"], "PD", "Notify PD immediately if the transport vehicle is disabled."),
            written("Describe the required vehicle setup after purchasing a DOC vehicle.", "Add DOC livery, use Steel Grey or Black body color, install extras with internal lights, and optionally add a ram bar. A light bar restricts the vehicle to inmate transports."),
            written("Outline the communications required when beginning a transport.", "Notify any correctional officers remaining at the prison, tell PD you are ready to move, and follow PD directions."),
            written("The prison approach is still under attack. What should the transport driver do?", "Drive inside only if gates are not blocked; otherwise follow the Scene Commander or police and wait at a safe location if directed."),
            written("When may a certified officer use an SMG or rifle at the gates?", "When authorized by Lieutenant+, authorized by SASP, being fired on by Class 3/4 weapons, or involved in a designated high-risk transport."),
        ],
    },
    {
        "slug": "visitation",
        "title": "Visitation",
        "summary": "Visitor screening, contraband, evidence logging, inmate searches, and HUT approval.",
        "questions": [
            mc("Who must approve visitation for HUT inmates?", ["Any FTO", "FIB/DOJ", "The visitor", "A cadet"], "FIB/DOJ", "HUT inmate visitation requires FIB/DOJ approval."),
            mc("What identification must a visitor present?", ["Any photo", "A valid, unexpired state-issued ID", "A work badge only", "No ID is required"], "A valid, unexpired state-issued ID", "All visitors need valid, unexpired state-issued identification."),
            mc("Where should the initial visitor search occur?", ["The yard", "The airlock hallway", "A cell", "The parking lot"], "The airlock hallway", "Bring the visitor to the airlock hallway and perform the search there."),
            mc("What happens if a visitor refuses the search?", ["They may enter with an escort", "They are not allowed into the prison", "Only their bags are searched", "PD must approve entry"], "They are not allowed into the prison", "Search consent is a condition of entry."),
            mc("What should happen when illegal items are found on a visitor?", ["Store them and allow entry", "Turn them away and notify PD", "Give them to the inmate", "Ignore registered drugs"], "Turn them away and notify PD", "Turn the visitor away from the property and notify PD immediately."),
            mc("Where are confiscated visitor items kept?", ["Officer vehicle", "Evidence locker", "Inmate property", "Front desk"], "Evidence locker", "Items are logged and placed in the evidence locker until returned."),
            written("Describe the complete visitor entry screening process.", "Verify valid ID and no mask, search the visitor in the airlock, refuse entry for rejected searches, log confiscated items and starting cash, and escort approved visitors."),
            written("A state worker has a registered firearm. What may DOC do?", "DOC may confiscate it for the visit and return it when the person leaves. EMS, DOJ, and State workers on official visits may retain less-lethal weapons for self-defence."),
            written("What must happen to an inmate before entering visitation?", "Conduct a full search and confiscate potential contraband such as phones, radios, drugs, or weapons."),
            written("Describe the visitor exit procedure.", "Search the visitor again, log the cash amount on exit, and return eligible confiscated items from evidence."),
        ],
    },
    {
        "slug": "riot-response",
        "title": "Riot & Prison Break Response",
        "summary": "Initial reporting, lockdown, tactical communications, recapture, and post-incident duties.",
        "questions": [
            mc("What information should the first riot report include?", ["Only the location", "Location, inmate count, and whether they are armed", "Officer names only", "The current weather"], "Location, inmate count, and whether they are armed", "Notify fellow officers of the location, number of inmates, and whether they are armed."),
            mc("Which radio frequency is used to request PD assistance?", ["1.00", "10.00", "DOC private only", "Any frequency"], "1.00", "The SOP directs officers to request PD assistance via radio frequency 1.00."),
            mc("If officers are overpowered, they should...", ["Charge alone", "Retreat to a safe area and coordinate", "Open every door", "End radio traffic"], "Retreat to a safe area and coordinate", "Retreat and coordinate with other correctional officers and police."),
            mc("During a riot, radio traffic should be...", ["Constant conversation", "Strictly necessary tactical communications", "Turned off", "Handled by inmates"], "Strictly necessary tactical communications", "Keep the radio to strictly necessary tac comms and request officer status checks."),
            mc("Where is lethal force permitted to stop an escaping inmate?", ["Anywhere in the city", "Only the outer perimeter", "Inside every cell", "Never"], "Only the outer perimeter", "The prison-break exception is limited to the outer perimeter."),
            mc("After the incident, who should be searched?", ["Only known participants", "All inmates", "Visitors only", "No one"], "All inmates", "All inmates are searched after the situation, regardless of involvement."),
            written("Describe your first actions when a riot begins.", "Report location, inmate count and weapons; request PD on 1.00 if needed; initiate lockdown when required; and coordinate with other officers."),
            written("Explain how separating prison sections helps regain control.", "Keeping sections closed prevents inmates from grouping together and lets officers retake areas one at a time."),
            written("What should happen when a rioting inmate is cuffed?", "Move the inmate to a cell or nearest lockable room and keep them cuffed if possible."),
            written("List the required post-riot actions and possible added punishments.", "Search every inmate, confiscate contraband, provide medical care, and consider sentence extension, cell time, or solitary confinement for participants."),
        ],
    },
]
