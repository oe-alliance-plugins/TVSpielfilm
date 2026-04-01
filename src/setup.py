from setuptools import setup
import setup_translate

pkg = 'Extensions.TVSpielfilm'
setup(name='enigma2-plugin-extensions-tvspielfilm',
       version='3.0',
       description='TV Spielfilm Plugin',
       package_dir={pkg: 'TVSpielfilm'},
       packages=[pkg],
       package_data={pkg: ['images/*.png', '*.png', '*.xml', 'locale/*/LC_MESSAGES/*.mo', 'db/tvs_mapping.txt', 'pics/FHD/icons/*.png', 'pics/FHD/logos/*.png', 'pics/HD/icons/*.png', 'pics/HD/logos/*.png']},
       cmdclass=setup_translate.cmdclass,  # for translation
      )
