(define (problem mountain-whisper)
    (:domain adventure-core)
    (:objects
        hero - player
        ancient_ruins - room
        mountain_cave - room
        ancient_relic - item
        spirit_key - item
        old_storyteller - character
    )
    (:init
        ;; Player starts in ancient ruins
        (at hero ancient_ruins)

        ;; Room connections
        (connected ancient_ruins mountain_cave)
        (connected mountain_cave ancient_ruins)

        ;; Items in rooms
        (item-at ancient_relic ancient_ruins)
        (item-at spirit_key mountain_cave)

        ;; NPC locations
        (npc-at old_storyteller ancient_ruins)

        ;; Item properties
        (weapon spirit_key)
        (vulnerable old_storyteller spirit_key)
        (path-key spirit_key)

        ;; Initial state flags
        (inspected ancient_ruins)
        (talked-to old_storyteller)
    )
    (:goal
        (and
            (defeated old_storyteller)
            (at hero mountain_cave)
        )
    )
)