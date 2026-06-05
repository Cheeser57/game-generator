(define (problem tiny-adventure-problem)

    (:domain tiny-adventure)

    (:objects

        hero - player

        kitchen hall - room

        key - item

        guard - character
    )

    (:init

        ;; player starts in hall
        (at hero hall)

        ;; rooms connected
        (connected hall kitchen)
        (connected kitchen hall)

        ;; key in kitchen
        (item-at key kitchen)

        ;; guard in hall
        (npc-at guard hall)
    )

    (:goal
        (game-won)
    )
)
