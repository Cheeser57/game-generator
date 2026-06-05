(define (problem dispute-begin)
    (:domain adventure-core)

    (:objects
        hero - player
        novigrad - room
        guild_hall - room
        negotiator_note - item
        mysterious_client - character
        guild_representative - character
    )

    (:init
        ;; player location
        (at hero novigrad)

        ;; room connections
        (connected novigrad guild_hall)
        (connected guild_hall novigrad)

        ;; item location
        (item-at negotiator_note novigrad)

        ;; npc location
        (npc-at mysterious_client novigrad)
        (npc-at guild_representative guild_hall)

        ;; quest-defined path unlock item
        (path-key negotiator_note)
    )

    (:goal
        (and
            (inspected novigrad)
            (talked-to mysterious_client)
            (item-at negotiator_note guild_hall)
        )
    )
)